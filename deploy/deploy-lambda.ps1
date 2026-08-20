# Deploy Arnifi RAG to AWS Lambda (container image + Function URL).
# Edit the variables below, then run from the project root:
#   .\deploy\deploy-lambda.ps1
#
# Prerequisites: Docker Desktop running, AWS CLI logged in, execution role ready.

$ErrorActionPreference = "Stop"

# ========== EDIT THESE ==========
$AwsRegion = "ap-south-1"              # Lambda + ECR region
$AccountId = "149689709685"            # 12-digit account id
$RepoName = "chatbot-arnifi-rag"       # ECR repository name (chatbot- prefix required)
$FunctionName = "chatbot-arnifi-rag-chat"
$RoleArn = "arn:aws:iam::${AccountId}:role/chatbot-lambda-execution-role"
$ImageTag = "latest"

# Bedrock may differ from Lambda region
$BedrockRegion = "us-east-1"
$BedrockChatModel = "amazon.nova-lite-v1:0"
$BedrockEmbedModel = "amazon.titan-embed-text-v2:0"

# Pinecone (same values as local .env)
$PineconeApiKey = $env:PINECONE_API_KEY
$PineconeIndex = "arnifi-rag-titan"
$PineconeEnvironment = "us-east-1"

# Periskope WhatsApp invite (same values as local .env)
$PeriskopeApiKey = $env:PERISKOPE_API_KEY
$PeriskopePhone = $env:PERISKOPE_PHONE
# ================================

if ($AccountId -eq "YOUR_AWS_ACCOUNT_ID" -or $RoleArn -match "YOUR_LAMBDA") {
    throw "Edit AccountId, FunctionName, and RoleArn at the top of deploy-lambda.ps1 first."
}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Load secrets from project .env into this process (does not overwrite existing env).
$EnvFile = Join-Path $Root ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $k = $Matches[1]
            $v = $Matches[2].Trim().Trim('"').Trim("'")
            if (-not [string]::IsNullOrWhiteSpace($v) -and -not [Environment]::GetEnvironmentVariable($k)) {
                [Environment]::SetEnvironmentVariable($k, $v, "Process")
            }
        }
    }
}

$PineconeApiKey = $env:PINECONE_API_KEY
$PeriskopeApiKey = $env:PERISKOPE_API_KEY
$PeriskopePhone = $env:PERISKOPE_PHONE

if (-not $PineconeApiKey) {
    throw "Set PINECONE_API_KEY in your environment (or .env) before deploying."
}
if (-not $PeriskopeApiKey -or -not $PeriskopePhone) {
    throw "Set PERISKOPE_API_KEY and PERISKOPE_PHONE in your environment (or .env) before deploying."
}

$PeriskopePhoneNorm = $PeriskopePhone.Trim()

$LambdaEnv =
    "Variables={" +
    "BEDROCK_REGION=$BedrockRegion," +
    "BEDROCK_CHAT_MODEL=$BedrockChatModel," +
    "BEDROCK_EMBED_MODEL=$BedrockEmbedModel," +
    "PINECONE_API_KEY=$PineconeApiKey," +
    "PINECONE_INDEX=$PineconeIndex," +
    "PINECONE_ENVIRONMENT=$PineconeEnvironment," +
    "PERISKOPE_API_KEY=$PeriskopeApiKey," +
    "PERISKOPE_PHONE=$PeriskopePhoneNorm," +
    "AWS_LWA_INVOKE_MODE=RESPONSE_STREAM," +
    "AWS_LWA_PORT=8080," +
    "AWS_LWA_READINESS_CHECK_PATH=/api/health," +
    "PORT=8080}"

$EcrUri = "$AccountId.dkr.ecr.$AwsRegion.amazonaws.com"
$ImageUri = "$EcrUri/${RepoName}:$ImageTag"

Write-Host "==> Ensuring ECR repository exists: $RepoName"
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
aws ecr describe-repositories --repository-names $RepoName --region $AwsRegion 2>$null | Out-Null
$repoMissing = ($LASTEXITCODE -ne 0)
$ErrorActionPreference = $prevEap
if ($repoMissing) {
    aws ecr create-repository --repository-name $RepoName --region $AwsRegion | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create ECR repo '$RepoName'. Ask admin for ecr:CreateRepository (or create the repo once)."
    }
}

Write-Host "==> Logging Docker into ECR"
$loginOut = aws ecr get-login-password --region $AwsRegion 2>&1
if ($LASTEXITCODE -ne 0 -or -not $loginOut) {
    throw "ECR login failed (need ecr:GetAuthorizationToken). Admin must grant ECR access to user chatbot."
}
$loginOut | docker login --username AWS --password-stdin $EcrUri
if ($LASTEXITCODE -ne 0) {
    throw "docker login to ECR failed."
}

Write-Host "==> Building Lambda image (Dockerfile.lambda, linux/amd64, no attestations)"
docker build `
    --platform linux/amd64 `
    --provenance=false `
    --sbom=false `
    -f Dockerfile.lambda `
    -t "${RepoName}:$ImageTag" .
if ($LASTEXITCODE -ne 0) { throw "docker build failed." }

Write-Host "==> Tagging and pushing $ImageUri"
docker tag "${RepoName}:$ImageTag" $ImageUri
docker push $ImageUri
if ($LASTEXITCODE -ne 0) {
    throw "docker push failed. Confirm ECR repo exists and chatbot has push permissions."
}

Write-Host "==> Creating or updating Lambda function $FunctionName"
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
aws lambda get-function --function-name $FunctionName --region $AwsRegion 2>$null | Out-Null
$exists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap

if (-not $exists) {
    aws lambda create-function `
        --function-name $FunctionName `
        --package-type Image `
        --code ImageUri=$ImageUri `
        --role $RoleArn `
        --timeout 90 `
        --memory-size 1024 `
        --region $AwsRegion `
        --environment $LambdaEnv |
        Out-Null
} else {
    aws lambda update-function-code `
        --function-name $FunctionName `
        --image-uri $ImageUri `
        --region $AwsRegion | Out-Null

    aws lambda wait function-updated --function-name $FunctionName --region $AwsRegion

    aws lambda update-function-configuration `
        --function-name $FunctionName `
        --timeout 90 `
        --memory-size 1024 `
        --region $AwsRegion `
        --environment $LambdaEnv |
        Out-Null
}

aws lambda wait function-updated --function-name $FunctionName --region $AwsRegion

Write-Host "==> Ensuring Function URL exists"
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
aws lambda get-function-url-config --function-name $FunctionName --region $AwsRegion 2>$null | Out-Null
$urlMissing = ($LASTEXITCODE -ne 0)
$ErrorActionPreference = $prevEap
if ($urlMissing) {
    # AuthType NONE is simplest for first test — protect or delete when done (bot risk / cost).
    # RESPONSE_STREAM + Lambda Web Adapter enables true SSE token streaming to the browser.
    aws lambda create-function-url-config `
        --function-name $FunctionName `
        --auth-type NONE `
        --invoke-mode RESPONSE_STREAM `
        --region $AwsRegion | Out-Null
} else {
    aws lambda update-function-url-config `
        --function-name $FunctionName `
        --invoke-mode RESPONSE_STREAM `
        --region $AwsRegion | Out-Null
}

# Public Function URL needs InvokeFunctionUrl (and InvokeFunction for some clients).
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
aws lambda add-permission `
    --function-name $FunctionName `
    --statement-id FunctionURLAllowPublicAccess `
    --action lambda:InvokeFunctionUrl `
    --principal "*" `
    --function-url-auth-type NONE `
    --region $AwsRegion 2>$null | Out-Null
aws lambda add-permission `
    --function-name $FunctionName `
    --statement-id FunctionURLAllowInvokeFunction `
    --action lambda:InvokeFunction `
    --principal "*" `
    --region $AwsRegion 2>$null | Out-Null
$ErrorActionPreference = $prevEap

$FunctionUrl = aws lambda get-function-url-config `
    --function-name $FunctionName `
    --region $AwsRegion `
    --query FunctionUrl `
    --output text

Write-Host ""
Write-Host "Deploy complete."
Write-Host "Function URL: $FunctionUrl"
Write-Host "Health check: ${FunctionUrl}api/health"
Write-Host "Open the Function URL in a browser for the chat UI."
