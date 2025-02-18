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
        self.user_name = None

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
        summary_prompt = f"Summarize this conversation briefly:\n{messages_text}"
        
        client = Groq(api_key=self.config['GROQ_API_KEY'])
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": summary_prompt}],
            model="llama3-70b-8192",
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
        self.chunk_embeddings = []
        self.chunk_texts = []
        self.embedding_model = SentenceTransformer('all-mpnet-base-v2')
        self.chunk_size = 512
        self.chunk_overlap = 128
        self.embeddings_file = 'embeddings_cache.pkl'
        self.hash_file = 'document_hash.txt'

    def get_document_hash(self, file_path: str) -> str:
        with open(file_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()

    def save_embeddings(self):
        cache_data = {
            'chunk_embeddings': self.chunk_embeddings,
            'chunk_texts': self.chunk_texts
        }
        with open(self.embeddings_file, 'wb') as f:
            pickle.dump(cache_data, f)
        
        if os.path.exists('scraping_results.docx'):
            with open(self.hash_file, 'w') as f:
                f.write(self.get_document_hash('scraping_results.docx'))

    def load_embeddings(self) -> bool:
        try:
            if not (os.path.exists(self.embeddings_file) or 
                   not os.path.exists(self.hash_file) or 
                   not os.path.exists('scraping_results.docx')):
                return False

            with open(self.hash_file, 'r') as f:
                cached_hash = f.read().strip()
            current_hash = self.get_document_hash('scraping_results.docx')
            
            if cached_hash != current_hash:
                return False

            with open(self.embeddings_file, 'rb') as f:
                cache_data = pickle.load(f)
                self.chunk_embeddings = cache_data['chunk_embeddings']
                self.chunk_texts = cache_data['chunk_texts']
            return True
        except Exception as e:
            print(f"Error loading embeddings: {e}")
            return False

    def preprocess_text(self, text: str) -> str:
        text = re.sub(r'\s+', ' ', text).strip()
        text = re.sub(r'[^a-zA-Z0-9\s.,!?-]', '', text)
        return text

    def create_chunks(self, text: str) -> List[str]:
        words = text.split()
        chunks = []
        
        for i in range(0, len(words), self.chunk_size - self.chunk_overlap):
            chunk = ' '.join(words[i:i + self.chunk_size])
            if chunk:
                chunks.append(chunk)
        return chunks

    def load_docx(self, file_path: str):
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Missing {file_path}")
        
        if self.load_embeddings():
            print("Loaded cached embeddings")
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

            current_page.all_content += f"\n{text}" if current_page.all_content else text

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
        if len(self.chunk_embeddings) == 0:
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
                self.save_embeddings()

    def find_similar_documents(self, query: str, top_k: int = 3) -> List[Dict]:
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
        self.first_interaction = True  # CRUCIAL ADDITION
        self.user_name = None  # CRUCIAL ADDITION

    def reset_conversation(self):
        self.conversation_buffer.messages = []
        self.conversation_buffer.summary_embeddings = []
        self.conversation_buffer.summaries = []
        self.first_interaction = True
        self.user_name = None

    def get_response(self, user_input: str) -> Tuple[str, List[Dict]]:
        self.conversation_buffer.add_message("user", user_input)
        conv_context = self.conversation_buffer.get_relevant_context(user_input)
        similar_docs = self.doc_store.find_similar_documents(user_input)
        
        doc_context_parts = []
        for doc in similar_docs:
            relevant_chunks = "\n".join([f"- {chunk['text']}" for chunk in doc['relevant_chunks'][:2]])
            doc_context_parts.append(
                f"Source: {doc['document']['title']}\n"
                f"URL: {doc['document']['url']}\n"
                f"Content:\n{relevant_chunks}"
            )
        
        doc_context = "\n\n".join(doc_context_parts)
        full_context = f"""Reference Info:
{doc_context}

Previous Conversation:
{conv_context}"""

        prompt = f"""You are SAM, a student consultant at Saint Peter's University. Be concise and helpful. Use the conversation history to maintain context. don't give any additional information. just the answer for the question asked by the user.

Context:
{full_context}

Question: {user_input}

Answer:"""

        try:
            response = self.groq_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": f"You are a student consultant. Be friendly and concise. User's name: {self.user_name}."
                    },
                    {"role": "user", "content": prompt}
                ],
                model="llama3-70b-8192",
                temperature=0.1
            )
            answer = response.choices[0].message.content.strip()
            self.conversation_buffer.add_message("assistant", answer)
            return answer, similar_docs
        except Exception as e:
            return f"Error: {str(e)}", []