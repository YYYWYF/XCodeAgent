# Agent Development Habits

- After every code change, check that the backend health endpoint is still healthy: `curl -sS http://127.0.0.1:8000/health`.
- When frontend development server is running, also check the active Vite URL after code changes. The default is `http://127.0.0.1:5173`; if Vite reports a different port, use that port instead.
- If either health check fails, investigate and fix it before reporting the change as done.
