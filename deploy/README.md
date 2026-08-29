# Lambda deploy — exact CLI / Docker steps

Local chat must already work. Set an AWS **Budget alert** ($5/$10) before a public Function URL.

## Step 0 — Install tools (required once)

This machine needs both **AWS CLI** and **Docker Desktop**.

### AWS CLI v2 (Windows)

1. Download: https://aws.amazon.com/cli/
2. Install the MSI, then **close and reopen** PowerShell.
3. Configure credentials (same keys as in `.env`, or SSO):

```powershell
aws --version
aws configure
# AWS Access Key ID: <from IAM>
# AWS Secret Access Key: <from IAM>
# Default region: ap-south-1
# Default output: json
```

4. Verify:

```powershell
aws sts get-caller-identity
```

Note the `Account` field — that is your 12-digit account ID.

### Docker Desktop (Windows)

1. Download: https://www.docker.com/products/docker-desktop/
2. Install, start Docker Desktop, wait until it says **Running**.
3. Verify:

```powershell
docker --version
docker info
```

---

## What you need ready

| Item | Example |
|------|---------|
| AWS account ID | from `aws sts get-caller-identity` |
| Lambda region | `ap-south-1` |
| Bedrock region | `us-east-1` |
| ECR repo name | `arnifi-rag` |
| Function name | `<your-prefix>-arnifi-rag-chat` |
| Execution role ARN | `arn:aws:iam::ACCOUNT:role/ROLE_NAME` |
| Docker Desktop | running |
| AWS CLI | `aws sts get-caller-identity` works |

Role must allow Bedrock `InvokeModel` / `Converse` (+ stream) and CloudWatch Logs.

---

## Option A — one script (after editing variables)

```powershell
cd "c:\Users\india\Desktop\Arnifi RAG Chatbot\Arnifi-Enterprise-RAG-Solution"
.\venv\Scripts\Activate.ps1

# Load Pinecone key from .env into the session (or set manually)
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*PINECONE_API_KEY\s*=\s*(.+)$') { $env:PINECONE_API_KEY = $Matches[1].Trim() }
}

# Edit AccountId, FunctionName, RoleArn inside the script first
notepad .\deploy\deploy-lambda.ps1
.\deploy\deploy-lambda.ps1
```

The script prints the **Function URL** at the end.

---

## Option B — manual commands (step by step)

Set variables once (PowerShell):

```powershell
cd "c:\Users\india\Desktop\Arnifi RAG Chatbot\Arnifi-Enterprise-RAG-Solution"

$AwsRegion = "ap-south-1"
$AccountId = "YOUR_12_DIGIT_ACCOUNT_ID"
$RepoName = "arnifi-rag"
$FunctionName = "YOUR_PREFIX-arnifi-rag-chat"
$RoleArn = "arn:aws:iam::${AccountId}:role/YOUR_EXECUTION_ROLE_NAME"
$EcrUri = "$AccountId.dkr.ecr.$AwsRegion.amazonaws.com"
$ImageUri = "$EcrUri/${RepoName}:latest"

# From your working .env
$env:PINECONE_API_KEY = "pcsk_..."   # paste your key
$PineconeIndex = "arnifi-rag-titan"
$BedrockRegion = "us-east-1"
```

### 1) Confirm AWS identity

```powershell
aws sts get-caller-identity
```

### 2) Create ECR repository (once)

```powershell
aws ecr create-repository --repository-name $RepoName --region $AwsRegion
```

Ignore error if it already exists.

### 3) Login Docker to ECR

```powershell
aws ecr get-login-password --region $AwsRegion | docker login --username AWS --password-stdin $EcrUri
```

### 4) Build the **Lambda** image

Use `Dockerfile.lambda` (AWS Lambda Python base + Mangum). Do **not** use the root `Dockerfile` for Lambda (that one is for ECS/uvicorn).

```powershell
docker build -f Dockerfile.lambda -t "${RepoName}:latest" .
docker tag "${RepoName}:latest" $ImageUri
docker push $ImageUri
```

### 5) Create Lambda (first time)

```powershell
aws lambda create-function `
  --function-name $FunctionName `
  --package-type Image `
  --code ImageUri=$ImageUri `
  --role $RoleArn `
  --timeout 60 `
  --memory-size 1024 `
  --region $AwsRegion `
  --environment "Variables={AWS_REGION=$AwsRegion,BEDROCK_REGION=$BedrockRegion,BEDROCK_CHAT_MODEL=amazon.nova-lite-v1:0,BEDROCK_EMBED_MODEL=amazon.titan-embed-text-v2:0,PINECONE_API_KEY=$env:PINECONE_API_KEY,PINECONE_INDEX=$PineconeIndex,PINECONE_ENVIRONMENT=us-east-1}"
```

### 6) Or update an existing function

```powershell
aws lambda update-function-code `
  --function-name $FunctionName `
  --image-uri $ImageUri `
  --region $AwsRegion

aws lambda wait function-updated --function-name $FunctionName --region $AwsRegion

aws lambda update-function-configuration `
  --function-name $FunctionName `
  --timeout 60 `
  --memory-size 1024 `
  --region $AwsRegion `
  --environment "Variables={AWS_REGION=$AwsRegion,BEDROCK_REGION=$BedrockRegion,BEDROCK_CHAT_MODEL=amazon.nova-lite-v1:0,BEDROCK_EMBED_MODEL=amazon.titan-embed-text-v2:0,PINECONE_API_KEY=$env:PINECONE_API_KEY,PINECONE_INDEX=$PineconeIndex,PINECONE_ENVIRONMENT=us-east-1}"
```

### 7) Function URL (public test — protect later)

```powershell
aws lambda create-function-url-config `
  --function-name $FunctionName `
  --auth-type NONE `
  --invoke-mode BUFFERED `
  --region $AwsRegion

aws lambda add-permission `
  --function-name $FunctionName `
  --statement-id FunctionURLAllowPublicAccess `
  --action lambda:InvokeFunctionUrl `
  --principal "*" `
  --function-url-auth-type NONE `
  --region $AwsRegion
```

### 8) Get the URL

```powershell
aws lambda get-function-url-config `
  --function-name $FunctionName `
  --region $AwsRegion `
  --query FunctionUrl `
  --output text
```

Open that URL in a browser. Health: append `api/health`.

---

## Cost / safety defaults

| Setting | Value |
|---------|--------|
| Memory | 1024 MB |
| Timeout | 60 s |
| Provisioned concurrency | Off |
| Auth | `NONE` only for short tests — bots can burn Bedrock $ |

Tear down when done testing:

```powershell
aws lambda delete-function-url-config --function-name $FunctionName --region $AwsRegion
aws lambda delete-function --function-name $FunctionName --region $AwsRegion
```

---

## Pricing lexical index (PM#### / Title rescue)

Lambda does **not** ship `data/artifacts/drive/extracted` (read-only image; artifacts remap to `/tmp`). After pricing CSV sync/ingest, rebuild and **commit** the bundled index so rescue works in prod:

```powershell
python -m app.cli build-pricing-index
# writes config/indexes/pricing_lexical.json (~1MB) — already COPY'd via config/
```

Then redeploy the Lambda image.

---

## Pre-deploy RAG smoke gate (optional but recommended)

Before shipping retrieval/prompt changes, from the repo root:

```powershell
# Offline (no AWS) — schema + metrics unit tests
python -m pytest tests/test_eval_metrics.py tests/test_eval_schema.py tests/test_eval_gate.py -q

# Live smoke vs frozen baseline (needs .env + Pinecone + Bedrock embed)
python scripts/eval_smoke_gate.py --tags smoke --max-drop 0.05
```

Exit code `1` means hit@k / recall (or faithfulness when enabled) dropped more than 0.05 vs `data/eval/baselines/baseline_v1.json`. Full `eval-rag` stays manual.

---

## ECS / EC2 later

Root `Dockerfile` +:

```text
uvicorn app.api.server:app --host 0.0.0.0 --port 8000
```

Same app code; no Mangum required.
