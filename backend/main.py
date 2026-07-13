from fastapi import FastAPI

from core.network import enable_system_trust_store

enable_system_trust_store()

app = FastAPI(title="LeadGen Platform")
