# Deployment Guide

This guide describes how to deploy the Ashwani Agent Company orchestration framework to VPS or cloud hosting environments (e.g., Render, Heroku).

## Environment Prerequisites

- **Python**: Version 3.9+
- **Git**: Installed and configured on PATH
- **SSH Key**: Configured for private repository clones
- **Supabase**: Active database project

## Deployment Steps

### 1. Repository Setup
Clone the parent repository to the deployment server:
```bash
git clone <repository_url> company
cd company
```

### 2. Environment Variables Configuration
Create a `.env` file from the example template:
```bash
cp .env.example .env
```
Fill in the following variables:
- `SUPABASE_URL`: Supabase project URL
- `SUPABASE_SERVICE_KEY`: Service role API key
- `BRIDGE_TOKEN`: Random alphanumeric token for API access

### 3. Dependency Installation
Create and activate a virtual environment, then install dependencies:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Running the Bridge Server
Expose the FastAPI API using Uvicorn:
```bash
uvicorn bridge_server:app --host 0.0.0.0 --port 8080
```

### 5. Running the Worker Daemon
Start the worker process using python:
```bash
python main.py
```
For production deployments, configure a process manager (such as `systemd` on Linux) to keep the worker running as a service:
```ini
[Unit]
Description=Ashwani Agent Worker Daemon
After=network.target

[Service]
Type=simple
User=deploy
WorkingDirectory=/home/deploy/company
ExecStart=/home/deploy/company/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
