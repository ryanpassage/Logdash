"""
Gunicorn configuration for Azure App Service.

Single worker is required: APScheduler runs in-process and must be a singleton.
Horizontal scaling requires migrating the collector to an Azure Function Timer Trigger.

Environment variables used:
  PORT — set automatically by Azure App Service (default 5000)
"""
import os

workers = 1
worker_class = "sync"
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
timeout = 120
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
# Forward X-Forwarded-For so Flask sees the real client IP behind App Service's proxy
forwarded_allow_ips = "*"
