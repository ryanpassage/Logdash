#!/bin/bash
# Azure App Service startup script.
# Set this as the "Startup Command" in App Service > Configuration > General settings,
# or reference it as a file: startup.sh
exec gunicorn -c gunicorn.conf.py app:app
