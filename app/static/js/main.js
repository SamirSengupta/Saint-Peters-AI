document.addEventListener('DOMContentLoaded', function() {
    const userInput = document.getElementById('user-input');
    const sendButton = document.getElementById('send-button');
    const chatHistory = document.getElementById('chat-history');
    const charCount = document.getElementById('char-count');

    // Load chat history from localStorage
    function loadChatHistory() {
        const conversations = JSON.parse(localStorage.getItem('chatHistory') || '[]');
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

        // Add user message to chat
        addMessageToChat(message, true);

        // Save to localStorage
        const conversations = JSON.parse(localStorage.getItem('chatHistory') || '[]');
        conversations.push({ message: message, isUser: true });
        
        // Clear input
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
                // Save AI response to localStorage
                conversations.push({ message: data.response, isUser: false });
                localStorage.setItem('chatHistory', JSON.stringify(conversations));
            } else {
                addMessageToChat('Sorry, there was an error processing your request.');
            }
        } catch (error) {
            console.error('Error:', error);
            addMessageToChat('Sorry, there was an error processing your request.');
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

    // Load chat history when page loads
    loadChatHistory();
});