# RunPod OCR Through Localhost

This project can now use a generic OpenAI-compatible OCR endpoint instead of being tied to Typhoon cloud keys.

Important:
- If you use RunPod, your documents are still processed on the RunPod GPU.
- An SSH tunnel keeps the app pointed at `localhost` and avoids exposing a public OCR HTTP endpoint, but it is not the same as fully local-only processing.

## 1. Deploy a RunPod GPU Pod

Recommended starting point:
- Template: a PyTorch or CUDA-enabled Linux image
- Access: enable SSH
- Ports: SSH is required; you do not need to expose the OCR HTTP port publicly if you will use an SSH tunnel

Good first test GPUs:
- `RTX 4090` for lower cost testing
- `L40S` if you want more VRAM headroom

## 2. Start an OpenAI-compatible OCR server on the Pod

SSH into the Pod, then install and start `vllm`.

```bash
python -m pip install --upgrade pip
python -m pip install vllm transformers
vllm serve scb10x/typhoon-ocr-7b --served-model-name scb10x/typhoon-ocr-7b:latest --dtype bfloat16 --host 127.0.0.1 --port 8101
```

Notes:
- The app must send the same model name returned by `/v1/models`. For the current OCR endpoint, use `scb10x/typhoon-ocr-7b:latest`.
- If you want to test a smaller model later, keep the same served model name so the app config does not need to change.

## 3. Open a localhost tunnel from your machine to the Pod

On your Windows machine, use the SSH host and port shown in the RunPod `Connect` dialog.

```powershell
ssh -N -L 8101:127.0.0.1:8101 root@<RUNPOD_HOST> -p <RUNPOD_SSH_PORT>
```

Keep this terminal open while testing.

After that, your local machine can call the remote OCR server at:

```text
http://127.0.0.1:8101/v1
```

## 4. Verify the tunnel before starting the app

In another terminal:

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:8101/v1/models | Select-Object -ExpandProperty Content
```

If the tunnel is healthy, you should see the served model list and `scb10x/typhoon-ocr-7b:latest` in the response.

## 5. Update `server/.env`

Use the new generic OCR variables:

```env
APP_CORS_ORIGINS=http://localhost:3000

MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_DATABASE=ocrstudio
MONGODB_JOBS_COLLECTION=ocr_jobs
MONGODB_IMPORTS_COLLECTION=ocr_imports

OCR_BASE_URL=http://127.0.0.1:8101/v1
OCR_MODEL=scb10x/typhoon-ocr-7b:latest
OCR_API_KEY=

# Leave these empty if you do not want structured extraction to call another service.
TEXT_BASE_URL=
TEXT_MODEL=
TEXT_API_KEY=

MAX_UPLOAD_MB=25
```

Important:
- MongoDB is now required for both OCR jobs and review records.
- If you do not want OCR text to go to another service, leave `TEXT_*` empty.
- Also remove old `TYPHOON_API_KEY` or `TYPHOON_OCR_API_KEY` values from `server/.env` if you do not want fallback to the old cloud setup.

## 6. Start the local app

Server:

```powershell
cd server
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

Client:

```powershell
cd client
npm run dev
```

Open:

```text
http://localhost:3000
```

## 7. What changed in the code

The app now supports:
- separate OCR and text extraction endpoints
- `OCR_MODEL` selection in `.env`
- localhost/private-network OCR endpoints without requiring an API key
- legacy `TYPHOON_*` endpoint and API key variables as fallback so the previous setup still works

## 8. Later, when you buy your own GPU

You can keep the same app config and just replace the tunnel target with your own local OCR server:

```env
OCR_BASE_URL=http://127.0.0.1:8101/v1
OCR_MODEL=scb10x/typhoon-ocr-7b:latest
OCR_API_KEY=
```

That means the application code does not need another migration when you move off RunPod.
