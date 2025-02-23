from dataclasses import dataclass
from typing import List, Dict, Tuple, Any
import re
import os
import pickle
import hashlib
import numpy as np
from sentence_transformers import SentenceTransformer
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table
from groq import Groq
from collections import defaultdict
import docx2txt
from datetime import datetime

def preprocess_text(text: str) -> str:
    """
    Clean and normalize text for embeddings.
    """
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]', '', text)
    text = text.replace('\t', ' ').replace('\n', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[^a-zA-Z0-9\s.,!?-]', '', text)
    return text.lower()

@dataclass
class PageContent:
    """
    Dataclass to store structured content of a document page.
    """
    title: str = ""
    url: str = ""
    meta_description: str = ""
    main_content: str = ""
    all_content: str = ""

class DocumentStore:
    """
    Manages document storage, chunking, and embeddings for semantic search.
    """
    def __init__(self):
        self.documents: List[PageContent] = []
        self.chunk_embeddings = []
        self.chunk_texts = []
        self.embedding_model = SentenceTransformer('all-mpnet-base-v2')
        self.chunk_size = 1024
        self.chunk_overlap = 256
        self.embeddings_file = 'embeddings_cache.pkl'
        self.hash_file = 'document_hash.txt'

    def get_document_hash(self, file_path: str) -> str:
        """Generate MD5 hash of the document for versioning."""
        with open(file_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()

    def save_embeddings(self):
        """Save embeddings and chunk texts to disk."""
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
        """
        Load cached embeddings if document hasn't changed.
        """
        try:
            if not (os.path.exists(self.embeddings_file) and
                   os.path.exists(self.hash_file) and
                   os.path.exists('scraping_results.docx')):
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

    def load_docx(self, file_path: str):
        """
        Parse DOCX file and extract structured content with fallback for complex files.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Missing {file_path}")
        
        if self.load_embeddings():
            print("Loaded cached embeddings")
            return

        print("Creating new embeddings...")
        current_page = PageContent()
        sections = defaultdict(lambda: {'title': '', 'content': ''})
        header_buffers = []
        footer_buffers = []
        current_section = None  # Initialize outside try block

        try:
            doc = Document(file_path)
            for elem in doc.element.body:
                if elem.tag.endswith('}p'):
                    paragraph = Paragraph(elem, doc)
                    if any(r.text.startswith("Header") for r in paragraph.runs):
                        header_buffers.append(paragraph.text)
                    elif any(r.text.startswith("Footer") for r in paragraph.runs):
                        footer_buffers.append(paragraph.text)
                    else:
                        if paragraph.style and paragraph.style.name.startswith('Heading'):
                            current_section = paragraph.text.upper().strip()
                        else:
                            processed = preprocess_text(paragraph.text)
                            sections[current_section or 'UNKNOWN']['content'] += '\n' + processed
                elif elem.tag.endswith('}tbl'):
                    table = Table(elem, doc)
                    for row in table.rows:
                        row_content = [cell.text.strip() for cell in row.cells]
                        processed_row = '\t'.join(row_content)
                        processed = preprocess_text(processed_row)
                        sections['TABLES']['content'] += '\n' + processed
        except Exception as e:
            print(f"Failed to parse {file_path} with python-docx: {e}. Falling back to docx2txt.")
            text = docx2txt.process(file_path)
            processed = preprocess_text(text)
            sections['UNKNOWN']['content'] = processed

        current_page.title = current_section or "Untitled"
        current_page.meta_description = "\n".join([sec['content'] for sec in sections.values()])[:150]
        current_page.main_content = "\n\n".join([sec['content'] for sec in sections.values()])
        current_page.all_content = "\n".join(["\n".join(header_buffers).strip(), current_page.main_content.strip(), "\n".join(footer_buffers).strip()])
        self.documents.append(current_page)

    def create_chunks(self, text: str, section_title: str) -> List[Dict]:
        """
        Split text into chunks with metadata.
        """
        words = preprocess_text(text).split()
        chunks = []
        for i in range(0, len(words), self.chunk_size - self.chunk_overlap):
            chunk_text = ' '.join(words[i:i + self.chunk_size])
            chunk = {'title': section_title, 'text': chunk_text, 'url': 'N/A (DOCX)'}
            chunks.append(chunk)
        return chunks

    def create_embeddings(self):
        """Generate and save embeddings for chunks."""
        if len(self.chunk_embeddings) == 0:
            self.chunk_embeddings = []
            self.chunk_texts = []
            
            for doc in self.documents:
                sections = {}
                current_section = "START"
                for part in doc.main_content.split('\n\n'):
                    if part.upper() in doc.main_content:
                        current_section = part.upper().strip()
                    else:
                        sections[current_section] = sections.get(current_section, '') + '\n' + part
                
                for section, content in sections.items():
                    chunks = self.create_chunks(content, section)
                    for chunk in chunks:
                        self.chunk_texts.append({'title': chunk['title'], 'text': chunk['text'], 'url': chunk['url']})
                
                if 'UNKNOWN' in sections:
                    chunks = self.create_chunks(sections['UNKNOWN'], 'UNKNOWN')
                    for chunk in chunks:
                        self.chunk_texts.append({'title': 'UNKNOWN', 'text': chunk['text'], 'url': 'N/A (DOCX)'})
            
            if self.chunk_texts:
                texts = [chunk['text'] for chunk in self.chunk_texts]
                self.chunk_embeddings = self.embedding_model.encode(
                    texts, batch_size=32, show_progress_bar=True, normalize_embeddings=True)
                self.save_embeddings()

    def find_similar_documents(self, query: str, top_k: int = 1) -> List[Dict]:
        """
        Find semantically similar documents based on query.
        """
        processed_query = preprocess_text(query)
        query_embedding = self.embedding_model.encode(processed_query, normalize_embeddings=True)
        
        similarities = np.dot(self.chunk_embeddings, query_embedding)
        top_indices = np.argsort(similarities)[-top_k:][::-1]
        
        doc_scores = defaultdict(lambda: {'score': 0.0, 'chunks': [], 'url': '', 'meta_description': '', 'main_content': ''})
        
        for idx in top_indices:
            chunk = self.chunk_texts[idx]
            doc_title = chunk['title']
            doc_scores[doc_title]['score'] += similarities[idx]
            doc_scores[doc_title]['chunks'].append({'text': chunk['text'], 'score': float(similarities[idx])})
            doc = next((d for d in self.documents if doc_title in d.main_content), None)
            if doc:
                doc_scores[doc_title]['url'] = doc.url
                doc_scores[doc_title]['meta_description'] = doc.meta_description
                doc_scores[doc_title]['main_content'] = doc.main_content
        
        sorted_docs = sorted(doc_scores.items(), key=lambda x: x[1]['score'], reverse=True)[:top_k]
        
        results = []
        for title, data in sorted_docs:
            results.append({
                "document": {"title": title, "url": data['url'], "meta_description": data['meta_description'], "main_content": data['main_content']},
                "similarity": float(data['score']),
                "relevant_chunks": data['chunks'][:1]  # Limit to 1 chunk
            })
        return results

class ConversationBuffer:
    """
    Manages conversation history and summaries.
    """
    def __init__(self, config, max_messages: int = 10):
        self.messages: List[Dict[str, str]] = []
        self.summary_embeddings = []
        self.summaries = []
        self.config = config
        self.max_messages = max_messages
        self.embedding_model = SentenceTransformer('all-mpnet-base-v2')

    def add_message(self, role: str, content: str):
        """
        Add a message to the conversation buffer.
        """
        message = {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
        self.messages.append(message)
        
        if len(self.messages) % 5 == 0:
            self._update_summaries()
        
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def _update_summaries(self):
        """
        Generate a summary for the last 5 messages using Groq API.
        """
        messages_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in self.messages[-5:]])
        summary_prompt = f"Summarize this conversation briefly:\n{messages_text}"
        
        client = Groq(api_key='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')
        response = client.chat.completions.create(
            messages=[{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": summary_prompt}],
            model="llama3-70b-8192",
            temperature=0.3,
            max_tokens=50  # Limit summary to 50 tokens
        )
        summary = response.choices[0].message.content.strip()
        
        summary_embedding = self.embedding_model.encode(summary, normalize_embeddings=True)
        self.summary_embeddings.append(summary_embedding)
        self.summaries.append(summary)

    def get_relevant_context(self, query: str, top_k: int = 1) -> str:
        """
        Retrieve relevant conversation summaries based on query.
        """
        if not self.summaries:
            return ""
        
        query_embedding = self.embedding_model.encode(query, normalize_embeddings=True)
        similarities = np.dot(self.summary_embeddings, query_embedding)
        
        top_indices = np.argsort(similarities)[-top_k:][::-1]
        relevant_summaries = [self.summaries[i] for i in top_indices]
        
        return "\n".join(relevant_summaries)

class Chatbot:
    """
    Main chatbot class for handling user interactions.
    """
    def __init__(self, doc_store: DocumentStore, config):
        self.doc_store = doc_store
        self.config = config
        self.groq_client = Groq(api_key='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')
        self.conversation_buffer = ConversationBuffer(config)
        self.first_interaction = True
        self.user_name = None

    def reset_conversation(self):
        """Reset conversation state and buffer."""
        self.conversation_buffer.messages = []
        self.conversation_buffer.summary_embeddings = []
        self.conversation_buffer.summaries = []
        self.first_interaction = True
        self.user_name = None

    def estimate_tokens(self, text: str) -> int:
        """Roughly estimate token count (1 token ≈ 4 characters)."""
        return len(text) // 4

    def get_response(self, user_input: str) -> Tuple[str, List[Dict]]:
        """
        Generate chatbot response based on user input with optimized token usage.
        """
        self.conversation_buffer.add_message("user", user_input)
        conv_context = self.conversation_buffer.get_relevant_context(user_input, top_k=1)
        similar_docs = self.doc_store.find_similar_documents(user_input, top_k=1)
        
        doc_context_parts = []
        for doc in similar_docs:
            relevant_chunks = "\n".join([f"- {chunk['text'][:512]}" for chunk in doc['relevant_chunks'][:1]])
            doc_context_parts.append(f"Source: {doc['document']['title']}\nContent:\n{relevant_chunks}")
        
        doc_context = "\n\n".join(doc_context_parts)
        full_context = f"Reference Info:\n{doc_context}\n\nPrevious Conversation:\n{conv_context}"
        
        full_prompt = f"Context:\n{full_context}\n\nQuestion: {user_input}\n\nAnswer:"
        if self.estimate_tokens(full_prompt) > 1500:  # Cap at ~1500 tokens to stay under 6000 TPM
            full_context = full_context[:4000]  # Truncate context
            full_prompt = f"Context:\n{full_context}\n\nQuestion: {user_input}\n\nAnswer:"
        
        try:
            response = self.groq_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": f"You are SAM, a student consultant at Saint Peter’s University. Be kind, concise, and professional. Use the conversation history to maintain context. Only provide information related to Saint Peter’s University. The user's name is {self.user_name}."
                    },
                    {"role": "user", "content": full_prompt}
                ],
                model="llama3-70b-8192",
                temperature=0.1,
                max_tokens=500  # Limit response length
            )
            answer = response.choices[0].message.content.strip()
            self.conversation_buffer.add_message("assistant", answer)
            return answer, similar_docs
        except Exception as e:
            return f"Error: {str(e)}", []