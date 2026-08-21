# Dialect Coach

Record yourself speaking English, see which sounds you got wrong, and get coached on how to fix them.

Pet project, not a product. In progress, nowhere near done.

## Run

```bash
make setup   # first time only
make up      # starts it at http://localhost:8501
```

Needs Docker. Fill in `.env` (created by `make setup`) with:

- `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` — required. Free F0 tier at [Azure Speech](https://portal.azure.com) (pick F0, not S0, which is not free).
- `GEMINI_API_KEY` — optional, for better coaching. Free at [Google AI Studio](https://aistudio.google.com/apikey).

## Test

```bash
make test    # run tests, offline, no API calls
make check   # lint + typecheck + test
```

