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
import docx2txt  # Added for fallback text extraction
from datetime import datetime

def preprocess_text(text: str) -> str:
    """
    Clean and normalize text for embeddings.
    
    - Removes control characters.
    - Replaces tabs and newlines with spaces.
    - Normalizes whitespace.
    - Removes special characters.
    - Converts text to lowercase.
    """
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]', '', text)  # Remove control characters
    text = text.replace('\t', ' ').replace('\n', ' ')  # Replace tabs and newlines
    text = re.sub(r'\s+', ' ', text).strip()  # Normalize whitespace
    text = re.sub(r'[^a-zA-Z0-9\s.,!?-]', '', text)  # Remove special characters
    return text.lower()  # Lowercase transformation

@dataclass
class PageContent:
    """
    Dataclass to store structured content of a document page.
    
    Fields:
    - title: The title of the page.
    - url: The URL of the page (if applicable).
    - meta_description: A short description of the content.
    - main_content: The main body of the content.
    - all_content: All content including headers and footers.
    """
    title: str = ""
    url: str = ""
    meta_description: str = ""
    main_content: str = ""
    all_content: str = ""

class DocumentStore:
    """
    Manages document storage, chunking, and embeddings for semantic search.
    
    - Loads and processes DOCX files.
    - Creates and caches embeddings for document chunks.
    - Provides methods to find similar documents based on queries.
    """
    def __init__(self):
        self.documents: List[PageContent] = []
        self.chunk_embeddings = []
        self.chunk_texts = []
        self.embedding_model = SentenceTransformer('all-mpnet-base-v2')
        self.chunk_size = 1024  # Adjust based on model capacity
        self.chunk_overlap = 256  # Overlap to preserve context
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
        
        # Save document hash for cache validation
        if os.path.exists('scraping_results.docx'):
            with open(self.hash_file, 'w') as f:
                f.write(self.get_document_hash('scraping_results.docx'))

    def load_embeddings(self) -> bool:
        """
        Load cached embeddings if document hasn't changed.
        
        Returns:
        - True if embeddings are loaded successfully.
        - False otherwise.
        """
        try:
            if not (os.path.exists(self.embeddings_file) and
                   os.path.exists(self.hash_file) and
                   os.path.exists('scraping_results.docx')):
                return False

            # Validate document hash
            with open(self.hash_file, 'r') as f:
                cached_hash = f.read().strip()
            current_hash = self.get_document_hash('scraping_results.docx')
            if cached_hash != current_hash:
                return False

            # Load embeddings and chunk texts
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
        
        - Attempts standard parsing with python-docx.
        - Falls back to docx2txt for plain text extraction if needed.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Missing {file_path}")
        
        if self.load_embeddings():
            print("Loaded cached embeddings")
            return

        print("Creating new embeddings...")
        current_page = PageContent()
        current_section = None
        sections = defaultdict(lambda: {
            'title': '',
            'content': ''
        })
        header_buffers = []
        footer_buffers = []

        # Try standard parsing with python-docx
        try:
            doc = Document(file_path)
            # Iterate over document elements
            for elem in doc.element.body:
                # Handle paragraphs
                if elem.tag.endswith('}p'):
                    paragraph = Paragraph(elem, doc)
                    # Check for headers/footers using placeholder text
                    if any(r.text.startswith("Header") for r in paragraph.runs):
                        header_buffers.append(paragraph.text)
                    elif any(r.text.startswith("Footer") for r in paragraph.runs):
                        footer_buffers.append(paragraph.text)
                    else:
                        # Process normal text content
                        if paragraph.style and paragraph.style.name.startswith('Heading'):
                            # Detect section headings
                            current_section = paragraph.text.upper().strip()
                        else:
                            # Extract and preprocess text
                            processed = preprocess_text(paragraph.text)
                            if current_section:
                                sections[current_section]['content'] += '\n' + processed
                            else:
                                sections['UNKNOWN']['content'] += '\n' + processed
                # Handle tables
                elif elem.tag.endswith('}tbl'):
                    table = Table(elem, doc)
                    for row in table.rows:
                        row_content = [cell.text.strip() for cell in row.cells]
                        processed_row = '\t'.join(row_content)
                        processed = preprocess_text(processed_row)
                        sections['TABLES']['content'] += '\n' + processed
        
        except Exception as e:
            print(f"Failed to parse {file_path} with python-docx: {e}")
            print("Falling back to plain text extraction with docx2txt...")
            # Fallback to docx2txt for text extraction
            try:
                text = docx2txt.process(file_path)
                processed = preprocess_text(text)
                sections['UNKNOWN']['content'] = processed  # Store all content in 'UNKNOWN' section
            except Exception as e:
                raise ValueError(f"Failed to process {file_path} even with fallback: {e}")

        # Construct page content
        current_page.title = current_section or "Untitled"
        current_page.meta_description = "\n".join([sec['content'] for sec in sections.values()])[:150]
        current_page.main_content = "\n\n".join([sec['content'] for sec in sections.values()])
        current_page.all_content = "\n".join([
            "\n".join(header_buffers).strip(),
            current_page.main_content.strip(),
            "\n".join(footer_buffers).strip()
        ])
        self.documents.append(current_page)

    def create_chunks(self, text: str, section_title: str) -> List[Dict]:
        """
        Split text into chunks with metadata.
        
        - Chunks are created with overlap to preserve context.
        - Each chunk includes title, text, and URL metadata.
        """
        words = preprocess_text(text).split()
        chunks = []
        for i in range(0, len(words), self.chunk_size - self.chunk_overlap):
            chunk_text = ' '.join(words[i:i + self.chunk_size])
            chunk = {
                'title': section_title,
                'text': chunk_text,
                'url': 'N/A (DOCX)'
            }
            chunks.append(chunk)
        return chunks

    def create_embeddings(self):
        """Generate and save embeddings for chunks."""
        if len(self.chunk_embeddings) == 0:
            self.chunk_embeddings = []
            self.chunk_texts = []
            
            for doc in self.documents:
                # Split main content into sections
                sections = {}
                current_section = "START"
                for part in doc.main_content.split('\n\n'):
                    if part.upper() in doc.main_content:
                        current_section = part.upper().strip()
                    else:
                        sections[current_section] = sections.get(current_section, '') + '\n' + part
                
                # Process each section
                for section, content in sections.items():
                    chunks = self.create_chunks(content, section)
                    for chunk in chunks:
                        self.chunk_texts.append({
                            'title': chunk['title'],
                            'text': chunk['text'],
                            'url': chunk['url']
                        })
                
                # Handle remaining sections
                if 'UNKNOWN' in sections:
                    chunks = self.create_chunks(sections['UNKNOWN'], 'UNKNOWN')
                    for chunk in chunks:
                        self.chunk_texts.append({
                            'title': 'UNKNOWN',
                            'text': chunk['text'],
                            'url': 'N/A (DOCX)'
                        })
            
            if self.chunk_texts:
                # Generate embeddings
                texts = [chunk['text'] for chunk in self.chunk_texts]
                self.chunk_embeddings = self.embedding_model.encode(
                    texts,
                    batch_size=32,
                    show_progress_bar=True,
                    normalize_embeddings=True
                )
                self.save_embeddings()

    def find_similar_documents(self, query: str, top_k: int = 3) -> List[Dict]:
        """
        Find semantically similar documents based on query.
        
        - Uses cosine similarity between query and chunk embeddings.
        - Returns top_k documents with relevant chunks and metadata.
        """
        processed_query = preprocess_text(query)
        query_embedding = self.embedding_model.encode(
            processed_query,
            normalize_embeddings=True
        )
        
        similarities = np.dot(self.chunk_embeddings, query_embedding)
        top_indices = np.argsort(similarities)[-10:][::-1]  # Top 10 chunks
        
        doc_scores = defaultdict(lambda: {
            'score': 0.0,
            'chunks': [],
            'url': '',
            'meta_description': '',
            'main_content': ''
        })
        
        for idx in top_indices:
            chunk = self.chunk_texts[idx]
            doc_title = chunk['title']
            doc_scores[doc_title]['score'] += similarities[idx]
            doc_scores[doc_title]['chunks'].append({
                'text': chunk['text'],
                'score': float(similarities[idx])
            })
            # Fetch document metadata
            doc = next((d for d in self.documents if doc_title in d.main_content), None)
            if doc:
                doc_scores[doc_title]['url'] = doc.url
                doc_scores[doc_title]['meta_description'] = doc.meta_description
                doc_scores[doc_title]['main_content'] = doc.main_content
        
        # Sort and compile results
        sorted_docs = sorted(
            doc_scores.items(),
            key=lambda x: x[1]['score'],
            reverse=True
        )[:top_k]
        
        results = []
        for title, data in sorted_docs:
            results.append({
                "document": {
                    "title": title,
                    "url": data['url'],
                    "meta_description": data['meta_description'],
                    "main_content": data['main_content']
                },
                "similarity": float(data['score']),
                "relevant_chunks": data['chunks'][:2]  # Top 2 chunks
            })
        
        return results

class ConversationBuffer:
    """
    Manages conversation history and summaries.
    
    - Stores messages with timestamps.
    - Generates summaries every 5 messages.
    - Provides methods to retrieve relevant context for queries.
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
        
        - Updates summaries if necessary.
        - Maintains max_messages limit by trimming old messages.
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        self.messages.append(message)
        
        if len(self.messages) % 5 == 0:
            self._update_summaries()
        
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def _update_summaries(self):
        """
        Generate a summary for the last 5 messages using Groq API.
        
        - Creates embeddings for summaries for context retrieval.
        """
        messages_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in self.messages[-5:]])
        summary_prompt = f"Summarize this conversation briefly:\n{messages_text}"
        
        # Hardcode the API key as requested
        client = Groq(api_key='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')
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": summary_prompt}
            ],
            model="llama3-70b-8192",
            temperature=0.3
        )
        summary = response.choices[0].message.content.strip()
        
        summary_embedding = self.embedding_model.encode(summary, normalize_embeddings=True)
        self.summary_embeddings.append(summary_embedding)
        self.summaries.append(summary)

    def get_relevant_context(self, query: str, top_k: int = 2) -> str:
        """
        Retrieve relevant conversation summaries based on query.
        
        - Uses cosine similarity to find similar summaries.
        - Returns concatenated summaries as context.
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
    
    - Integrates document store for knowledge retrieval.
    - Maintains conversation history via ConversationBuffer.
    - Generates responses using Groq API.
    """
    def __init__(self, doc_store: DocumentStore, config):
        self.doc_store = doc_store
        self.config = config
        # Hardcode the API key as requested
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

    def get_response(self, user_input: str) -> Tuple[str, List[Dict]]:
        """
        Generate chatbot response based on user input.
        
        - Retrieves relevant documents and conversation context.
        - Uses Groq API to generate a concise, helpful response.
        """
        self.conversation_buffer.add_message("user", user_input)
        conv_context = self.conversation_buffer.get_relevant_context(user_input)
        similar_docs = self.doc_store.find_similar_documents(user_input)
        
        doc_context_parts = []
        for doc in similar_docs:
            relevant_chunks = "\n".join([f"- {chunk['text']}" for chunk in doc['relevant_chunks']])
            doc_context_parts.append(
                f"Source: {doc['document']['title']}\n"
                f"URL: {doc['document']['url']}\n"
                f"Content:\n{relevant_chunks}"
            )
        
        doc_context = "\n\n".join(doc_context_parts)
        full_context = f"Reference Info:\n{doc_context}\n\nPrevious Conversation:\n{conv_context}"

        prompt = f"You are SAM, a student consultant at Saint Peter’s University. Be concise and helpful. Use the conversation history to maintain context. Only provide information related to Saint Peter’s University.\n\nContext:\n{full_context}\n\nQuestion: {user_input}\n\nAnswer:"
        
        try:
            response = self.groq_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": f"You are a helpful student consultant. Be kind, concise, and professional. The user's name is {self.user_name}."
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