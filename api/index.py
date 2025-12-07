# Vercel Python Serverless Function entrypoint
# It imports the FastAPI ASGI app from your project so Vercel can serve it.
# Docs: https://vercel.com/docs/functions/serverless-functions/runtimes/python

from main import app  # FastAPI instance

# Vercel will detect the `app` symbol and run it as an ASGI application.
