#!/bin/bash
# ASSUREDChain Launcher
# Double-click to start the app

cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate

# Start the backend API in background
echo "Starting ASSUREDChain Assistant API..."
python -m uvicorn assistant.backend.main:app --host 127.0.0.1 --port 8000 > /dev/null 2>&1 &
BACKEND_PID=$!

# Wait a moment for backend to start
sleep 2

# Start Streamlit
echo "Starting ASSUREDChain UI..."
echo "Opening browser at http://localhost:8503"
streamlit run app/Home.py --server.port 8503

# Cleanup: kill backend when Streamlit stops
kill $BACKEND_PID 2>/dev/null
