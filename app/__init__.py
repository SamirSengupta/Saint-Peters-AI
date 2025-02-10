from flask import Flask
from config import Config

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    from app.routes import main
    app.register_blueprint(main)
    
    with app.app_context():
        from app.models import DocumentStore, Chatbot
        app.chatbot = Chatbot(DocumentStore(), app.config)
        app.chatbot.doc_store.load_docx("scraping_results.docx")
        app.chatbot.doc_store.create_embeddings()
    
    return app