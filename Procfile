# Fallback for buildpack platforms without Docker. The corpus has to be built
# at boot here, because buildpack filesystems do not carry build output into
# the running dyno.
web: python -m engine.generate --seed 7 --size 80 && uvicorn web.app:app --host 0.0.0.0 --port $PORT
