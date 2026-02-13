# backend-service (V1.1)

独立轻后端服务，提供：
- `GET /web/status`
- `GET /web/models`
- `POST /api/v1/models/import-astr`
- `POST /api/v1/executor/jobs`

## 快速启动

```bash
cd /root/openclaw-astr-assistant/backend-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example /root/openclaw-assistant-backend/.env
export BACKEND_ENV_FILE=/root/openclaw-assistant-backend/.env
uvicorn app.main:app --host 127.0.0.1 --port 18889
```

如果 `python3 -m venv` 报错 `ensurepip is not available`，先安装：

```bash
apt update && apt install -y python3.12-venv
```

## 默认安全策略

- API 统一 Bearer Token 鉴权。
- Web/API 仅建议监听 `127.0.0.1`。
- 模型同步只保存元数据，不保存密钥。
- 执行器并发固定 `1`，Gemini 在 V1.1 预留未启用。
