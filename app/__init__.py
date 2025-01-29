from flask import Flask
from config import Config
from app.models import DocumentStore, Chatbot

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Initialize DocumentStore and Chatbot
    doc_store = DocumentStore()
    doc_store.load_docx("scraping_results.docx")
    doc_store.create_embeddings()
    
    app.chatbot = Chatbot(doc_store, app.config)
    
    from app.routes import main
    app.register_blueprint(main)
    
    return app