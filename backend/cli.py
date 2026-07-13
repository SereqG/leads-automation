from apps.pipeline.cli import app
from core.network import enable_system_trust_store

if __name__ == "__main__":
    enable_system_trust_store()
    app()
