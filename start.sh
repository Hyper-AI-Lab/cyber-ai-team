#!/usr/bin/env bash
# Cyber-Team startup script for screen session
# Usage: ./start.sh
# Attach: screen -r cyber-team

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Auto-detect server IP for remote access
HOST_IP="${HOST_IP:-$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost')}"
export HOST_IP

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  No .env file found. Copying from .env.example..."
    cp .env.example .env
    echo "📝 Please edit .env with your API keys before starting."
    echo "   Required: MISTRAL_API_KEY"
    echo ""
    read -p "Press Enter to continue or Ctrl+C to edit .env first..."
fi

# Check if already running in screen
if screen -list | grep -q "cyber-team"; then
    echo "✅ Cyber-Team is already running in screen session."
    echo "   Attach with: screen -r cyber-team"
    exit 0
fi

echo "🚀 Starting Cyber-Team in screen session..."
echo "   Server IP: $HOST_IP"
screen -dmS cyber-team bash -c "
    cd $SCRIPT_DIR
    export HOST_IP=$HOST_IP
    echo '═══════════════════════════════════════════════════════'
    echo '  Cyber-Team — AI Company Operating System'
    echo '═══════════════════════════════════════════════════════'
    echo ''
    docker compose up --build 2>&1 | while IFS= read -r line; do
        echo \"\$line\"
    done
" 2>/dev/null

echo "✅ Cyber-Team started in screen session 'cyber-team'"
echo ""
echo "📌 Commands:"
echo "   Attach:    screen -r cyber-team"
echo "   Detach:    Ctrl+A, D"
echo "   Stop:      screen -S cyber-team -X quit; docker compose down"
echo "   Logs:      docker compose logs -f core"
echo ""
echo "🌐 URLs (local):"
echo "   Console:   http://localhost:3001"
echo "   API:       http://localhost:8000"
echo "   API Docs:  http://localhost:8000/docs"
echo ""
echo "🌐 URLs (remote — from your PC):"
echo "   Console:   http://$HOST_IP:3001"
echo "   API:       http://$HOST_IP:8000"
echo "   API Docs:  http://$HOST_IP:8000/docs"
echo "   Grafana:   http://$HOST_IP:3500"
echo "   Langfuse:  http://$HOST_IP:3100"
echo ""
echo "💡 Make sure firewall allows ports: 3001, 8000, 3500, 3100"
