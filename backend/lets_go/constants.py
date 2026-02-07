import os

url = os.getenv('APP_BASE_URL', '')
SUPABASE_EDGE_API_KEY = os.getenv('SUPABASE_EDGE_API_KEY', '')
orsApiKey = os.getenv('OPENROUTESERVICE_API_KEY', '')