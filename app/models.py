from sentence_transformers import SentenceTransformer
from groq import Groq
import numpy as np
from typing import List, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime
from docx import Document
import re
import os
import pickle
import hashlib

@dataclass
class ChatMessage:
    role: str
    content: str
    timestamp: datetime

@dataclass
class PageContent:
    title: str = ""
    url: str = ""
    meta_description: str = ""
    main_content: str = ""
    all_content: str = ""

class ConversationBuffer:
    def __init__(self, config, max_messages: int = 10):
        self.messages: List[ChatMessage] = []
        self.max_messages = max_messages
        self.embedding_model = SentenceTransformer('all-mpnet-base-v2')
        self.summary_embeddings = []
        self.summaries = []
        self.config = config
        self.user_name = None  # Store the user's name

    def add_message(self, role: str, content: str):
        message = ChatMessage(
            role=role,
            content=content,
            timestamp=datetime.now()
        )
        self.messages.append(message)
        
        if len(self.messages) % 5 == 0:
            self._update_summaries()
        
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def _update_summaries(self):
        messages_text = "\n".join([f"{msg.role}: {msg.content}" for msg in self.messages[-5:]])
        summary_prompt = f"Please summarize this conversation briefly:\n{messages_text}"
        
        client = Groq(api_key=self.config['GROQ_API_KEY'])
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": summary_prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.3
        )
        summary = response.choices[0].message.content.strip()
        
        summary_embedding = self.embedding_model.encode(summary, normalize_embeddings=True)
        self.summary_embeddings.append(summary_embedding)
        self.summaries.append(summary)

    def get_relevant_context(self, query: str, top_k: int = 2) -> str:
        if not self.summaries:
            return ""
        
        query_embedding = self.embedding_model.encode(query, normalize_embeddings=True)
        similarities = np.dot(self.summary_embeddings, query_embedding)
        
        top_indices = np.argsort(similarities)[-top_k:][::-1]
        relevant_summaries = [self.summaries[i] for i in top_indices]
        
        return "\n".join(relevant_summaries)

class DocumentStore:
    def __init__(self):
        self.documents: List[PageContent] = []
        self.chunk_embeddings = []  # Initialize as empty list
        self.chunk_texts = []
        self.embedding_model = SentenceTransformer('all-mpnet-base-v2')
        self.chunk_size = 512
        self.chunk_overlap = 128
        self.embeddings_file = 'embeddings_cache.pkl'
        self.hash_file = 'document_hash.txt'

    def get_document_hash(self, file_path: str) -> str:
        """Calculate MD5 hash of the document file"""
        with open(file_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()

    def save_embeddings(self):
        """Save embeddings and chunks to file"""
        cache_data = {
            'chunk_embeddings': self.chunk_embeddings,
            'chunk_texts': self.chunk_texts
        }
        with open(self.embeddings_file, 'wb') as f:
            pickle.dump(cache_data, f)
        
        # Save document hash
        if os.path.exists('scraping_results.docx'):
            with open(self.hash_file, 'w') as f:
                f.write(self.get_document_hash('scraping_results.docx'))

    def load_embeddings(self) -> bool:
        """Load embeddings from cache if available and valid"""
        try:
            # Check if cache files exist
            if not (os.path.exists(self.embeddings_file) and 
                   os.path.exists(self.hash_file) and 
                   os.path.exists('scraping_results.docx')):
                return False

            # Check if document has changed
            with open(self.hash_file, 'r') as f:
                cached_hash = f.read().strip()
            current_hash = self.get_document_hash('scraping_results.docx')
            
            if cached_hash != current_hash:
                return False

            # Load cached embeddings
            with open(self.embeddings_file, 'rb') as f:
                cache_data = pickle.load(f)
                self.chunk_embeddings = cache_data['chunk_embeddings']
                self.chunk_texts = cache_data['chunk_texts']
            return True
        except Exception as e:
            print(f"Error loading embeddings cache: {e}")
            return False

    def preprocess_text(self, text: str) -> str:
        """Clean and normalize text"""
        text = re.sub(r'\s+', ' ', text).strip()
        text = re.sub(r'[^a-zA-Z0-9\s.,!?-]', '', text)
        return text

    def create_chunks(self, text: str) -> List[str]:
        """Split text into overlapping chunks"""
        words = text.split()
        chunks = []
        
        for i in range(0, len(words), self.chunk_size - self.chunk_overlap):
            chunk = ' '.join(words[i:i + self.chunk_size])
            if chunk:
                chunks.append(chunk)
        return chunks

    def load_docx(self, file_path: str):
        """Load and parse DOCX file"""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Could not find {file_path}")
        
        # Try to load cached embeddings first
        if self.load_embeddings():
            print("Using cached embeddings")
            return

        print("Creating new embeddings...")
        doc = Document(file_path)
        current_page = PageContent()
        collecting_main_content = False
        main_content_buffer = []
        
        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue

            if current_page.all_content:
                current_page.all_content += f"\n{text}"
            else:
                current_page.all_content = text

            if text.startswith("Page"):
                if current_page.title:
                    if main_content_buffer:
                        current_page.main_content = " ".join(main_content_buffer)
                    self.documents.append(current_page)
                    current_page = PageContent()
                    main_content_buffer = []
                current_page.title = text
                collecting_main_content = False

            elif text.startswith("URL:"):
                current_page.url = text.replace("URL:", "").strip()
                collecting_main_content = False

            elif text.startswith("Meta Description"):
                collecting_main_content = False
                continue
            elif not current_page.meta_description and not text.startswith(("Page", "URL:", "Main Content")):
                current_page.meta_description = text

            elif text == "Main Content":
                collecting_main_content = True
                continue

            elif collecting_main_content and not any(text.startswith(x) for x in ["Page", "URL:", "Contact Information"]):
                main_content_buffer.append(text)

            elif text.startswith("Contact Information"):
                collecting_main_content = False

        if current_page.title:
            if main_content_buffer:
                current_page.main_content = " ".join(main_content_buffer)
            self.documents.append(current_page)

    def create_embeddings(self):
        """Create embeddings only if they haven't been loaded from cache"""
        if len(self.chunk_embeddings) == 0:  # Check length instead of truth value
            self.chunk_embeddings = []
            self.chunk_texts = []
            
            for doc in self.documents:
                combined_text = f"Title: {doc.title} Description: {doc.meta_description} Content: {doc.main_content}"
                processed_text = self.preprocess_text(combined_text)
                chunks = self.create_chunks(processed_text)
                
                for chunk in chunks:
                    self.chunk_texts.append({
                        'text': chunk,
                        'doc_title': doc.title,
                        'doc_url': doc.url
                    })
            
            if self.chunk_texts:
                texts = [chunk['text'] for chunk in self.chunk_texts]
                self.chunk_embeddings = self.embedding_model.encode(
                    texts,
                    batch_size=32,
                    show_progress_bar=True,
                    normalize_embeddings=True
                )
                
                # Save the new embeddings to cache
                self.save_embeddings()

    def find_similar_documents(self, query: str, top_k: int = 3) -> List[Dict]:
        """Find documents similar to the query"""
        processed_query = self.preprocess_text(query)
        query_embedding = self.embedding_model.encode(
            processed_query,
            normalize_embeddings=True
        )
        
        similarities = np.dot(self.chunk_embeddings, query_embedding)
        top_indices = np.argsort(similarities)[-top_k:][::-1]
        
        doc_scores = {}
        for idx in top_indices:
            chunk = self.chunk_texts[idx]
            doc_title = chunk['doc_title']
            
            if doc_title not in doc_scores:
                doc_scores[doc_title] = {
                    'score': similarities[idx],
                    'url': chunk['doc_url'],
                    'chunks': []
                }
            
            doc_scores[doc_title]['chunks'].append({
                'text': chunk['text'],
                'score': float(similarities[idx])
            })

        results = []
        for title, data in doc_scores.items():
            doc = next((d for d in self.documents if d.title == title), None)
            if doc:
                results.append({
                    "document": {
                        "title": title,
                        "url": data['url'],
                        "meta_description": doc.meta_description,
                        "main_content": doc.main_content
                    },
                    "similarity": float(data['score']),
                    "relevant_chunks": data['chunks']
                })
        
        return sorted(results, key=lambda x: x['similarity'], reverse=True)

class Chatbot:
    def __init__(self, doc_store: DocumentStore, config):
        self.doc_store = doc_store
        self.groq_client = Groq(api_key=config['GROQ_API_KEY'])
        self.conversation_buffer = ConversationBuffer(config)
        self.first_interaction = True
        self.user_name = None  # Add this line to store the user's name

    def get_response(self, user_input: str) -> Tuple[str, List[Dict]]:
        """Generate a response to user input"""
        if self.first_interaction:
            self.first_interaction = False
            return "Hello! I'm SAM, your student consultant at Saint Peter's University. What's your name?", []

        if self.user_name is None:
            # Store the user's name
            self.user_name = user_input
            return f"Nice to meet you, {user_input}! How can I assist you today?", []

        # Add the user's message to the conversation buffer
        self.conversation_buffer.add_message("user", user_input)

        # Get relevant context from the conversation buffer
        conv_context = self.conversation_buffer.get_relevant_context(user_input)
        similar_docs = self.doc_store.find_similar_documents(user_input)
        
        doc_context_parts = []
        for doc in similar_docs:
            relevant_chunks = "\n".join([
                f"- {chunk['text']}"
                for chunk in doc['relevant_chunks'][:2]
            ])
            
            doc_context_parts.append(
                f"Source: {doc['document']['title']}\n"
                f"URL: {doc['document']['url']}\n"
                f"Content:\n{relevant_chunks}"
            )
        
        doc_context = "\n\n".join(doc_context_parts)
        full_context = f"""Reference Information:
{doc_context}

Previous Conversation Context:
{conv_context}"""

        prompt = f"""You are SAM, a friendly and conversational student consultant at Saint Peter's University. Your goal is to provide helpful, accurate, and concise responses to students. Avoid unnecessary details and focus on answering the question directly. The user's name is {self.user_name}. Dont greet the user again once you have done.

Context:
{full_context}

Question: {user_input}

Answer:"""

        try:
            response = self.groq_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": f"You are a friendly and conversational student consultant at Saint Peter's University. Your goal is to provide helpful, accurate, and concise responses to students. Avoid unnecessary details and focus on answering the question directly. The user's name is {self.user_name}."
                    },
                    {"role": "user", "content": prompt}
                ],
                model="llama-3.3-70b-versatile",
                temperature=0.3
            )
            answer = response.choices[0].message.content.strip()
            
            # Add the assistant's response to the conversation buffer
            self.conversation_buffer.add_message("assistant", answer)
            
            return answer, similar_docs
        except Exception as e:
            return f"Error: {str(e)}", []