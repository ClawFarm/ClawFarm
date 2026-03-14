from routes.auth import router as auth_router
from routes.bots import router as bots_router
from routes.fleet import router as fleet_router
from routes.terminal import router as terminal_router

all_routers = [auth_router, bots_router, fleet_router, terminal_router]
