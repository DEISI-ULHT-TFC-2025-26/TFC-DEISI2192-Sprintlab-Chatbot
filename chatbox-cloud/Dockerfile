# SprintLab TFC Chatbox — Hugging Face Docker Space
# Runs server.py (Groq-backed) on port 7860, the port HF Spaces routes to.

FROM python:3.11-slim

# HF Spaces run containers as a non-root user with uid 1000.
RUN useradd -m -u 1000 user
USER user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PORT=7860

WORKDIR /home/user/app

# Only the runtime files — secrets come from Space Secrets at runtime.
# *.py apanha o entrypoint (server.py) e os módulos (config, gitlab_api,
# analytics, report, charts, blame, actions, llm).
COPY --chown=user *.py chatbox.html style.css app.js ./

EXPOSE 7860

CMD ["python", "server.py"]
