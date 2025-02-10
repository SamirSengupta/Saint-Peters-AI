document.addEventListener('DOMContentLoaded', function() {
    const userInput = document.getElementById('user-input');
    const sendButton = document.getElementById('send-button');
    const chatHistory = document.getElementById('chat-history');
    const charCount = document.getElementById('char-count');

    if (!sessionStorage.getItem('chatHistory')) {
        sessionStorage.setItem('chatHistory', JSON.stringify([]));
    }

    function loadChatHistory() {
        const conversations = JSON.parse(sessionStorage.getItem('chatHistory') || '[]');
        conversations.forEach(conv => {
            addMessageToChat(conv.message, conv.isUser);
        });
    }

    function updateCharCount() {
        const length = userInput.value.length;
        charCount.textContent = `${length}/4000`;
    }

    function addMessageToChat(message, isUser = false) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `p-4 rounded-lg mb-4 ${isUser ? 'bg-slate-100 ml-12' : 'bg-gray-50 mr-12'}`;
        messageDiv.textContent = message;
        chatHistory.appendChild(messageDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    async function sendMessage() {
        const message = userInput.value.trim();
        if (!message) return;

        addMessageToChat(message, true);
        const conversations = JSON.parse(sessionStorage.getItem('chatHistory') || '[]');
        conversations.push({ message: message, isUser: true });
        
        userInput.value = '';
        updateCharCount();

        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message: message })
            });

            const data = await response.json();
            
            if (response.ok) {
                addMessageToChat(data.response);
                conversations.push({ message: data.response, isUser: false });
                sessionStorage.setItem('chatHistory', JSON.stringify(conversations));
            } else {
                addMessageToChat('Error processing request.');
            }
        } catch (error) {
            console.error('Error:', error);
            addMessageToChat('Request failed.');
        }
    }

    userInput.addEventListener('input', updateCharCount);
    sendButton.addEventListener('click', sendMessage);
    userInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    loadChatHistory();
});