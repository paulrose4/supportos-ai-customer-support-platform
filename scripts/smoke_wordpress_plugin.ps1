[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SiteKey,

    [string]$Docker = "docker",
    [string]$ComposeFile = "docker-compose.wordpress-test.yml",
    [string]$SiteUrl = "http://localhost:8085",
    [string]$AgentApiUrl = "http://api:8000",
    [string]$KnowledgeQuestion = "如何启用产品功能？"
)

$ErrorActionPreference = "Stop"
$plugin = "company-product-support-agent"
$httpHandler = [System.Net.Http.HttpClientHandler]::new()
$httpHandler.UseProxy = $false
$httpClient = [System.Net.Http.HttpClient]::new($httpHandler)

function Invoke-Docker {
    param([string[]]$Arguments)

    & $Docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE."
    }
}

function Invoke-WordPressCli {
    param([string[]]$Arguments)

    Invoke-Docker -Arguments (@("compose", "-f", $ComposeFile, "--profile", "tools", "run", "--rm", "wordpress-cli") + $Arguments)
}

Write-Host "Starting WordPress integration environment..."
Invoke-Docker -Arguments @("network", "inspect", "obsidianragagent_default") *> $null
Invoke-Docker -Arguments @("compose", "-f", $ComposeFile, "up", "-d", "wordpress-db", "wordpress")

$ready = $false
for ($attempt = 1; $attempt -le 60; $attempt++) {
    try {
        $response = $httpClient.GetAsync($SiteUrl).GetAwaiter().GetResult()
        if ($response.IsSuccessStatusCode) {
            $ready = $true
            break
        }
    }
    catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $ready) {
    throw "WordPress did not become ready at $SiteUrl."
}

& $Docker compose -f $ComposeFile --profile tools run --rm wordpress-cli core is-installed *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-WordPressCli -Arguments @(
        "core",
        "install",
        "--url=$SiteUrl",
        "--title=CPSA Plugin Test",
        "--admin_user=admin",
        "--admin_password=local-test-password",
        "--admin_email=admin@example.test",
        "--skip-email"
    )
}

Invoke-WordPressCli -Arguments @("plugin", "activate", $plugin)

$options = @{
    enabled = $true
    api_base_url = $AgentApiUrl
    site_key = $SiteKey
    widget_title = "Product Support"
    welcome_message = "Ask about our products."
    primary_color = "#0f766e"
    position = "right"
} | ConvertTo-Json -Compress
Invoke-WordPressCli -Arguments @("option", "update", "cpsa_options", $options, "--format=json")

Write-Host "Checking frontend credential isolation..."
$pageResponse = $httpClient.GetAsync($SiteUrl).GetAwaiter().GetResult()
$pageResponse.EnsureSuccessStatusCode() | Out-Null
$page = $pageResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
if ($page -notmatch "cpsa-widget-js") {
    throw "Widget JavaScript was not rendered on the WordPress frontend."
}
if ($page -notmatch "company-product-support-agent.+chat") {
    throw "Widget proxy endpoint was not rendered on the WordPress frontend."
}
if ($page.Contains($SiteKey)) {
    throw "Site key leaked into the WordPress frontend."
}

$endpoint = "$SiteUrl/index.php?rest_route=/company-product-support-agent/v1/chat"

function Invoke-WidgetChat {
    param([string]$Message)

    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, $endpoint)
    $request.Headers.Add("Origin", $SiteUrl)
    $body = @{ message = $Message } | ConvertTo-Json -Compress
    $request.Content = [System.Net.Http.StringContent]::new(
        $body,
        [System.Text.Encoding]::UTF8,
        "application/json"
    )
    $response = $httpClient.SendAsync($request).GetAwaiter().GetResult()
    $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) {
        throw "Widget request failed with HTTP $([int]$response.StatusCode): $content"
    }
    return $content | ConvertFrom-Json
}

Write-Host "Checking tenant knowledge retrieval..."
$knowledgeResponse = Invoke-WidgetChat -Message $KnowledgeQuestion
if ($knowledgeResponse.kind -ne "answer" -or $knowledgeResponse.citations.Count -lt 1) {
    throw "Knowledge request did not return a grounded answer with citations."
}

Write-Host "Checking anonymous business-data boundary..."
$orderResponse = Invoke-WidgetChat -Message "查询我的订单状态"
if ($orderResponse.kind -ne "clarification") {
    throw "Anonymous order request did not require trusted authentication."
}

Write-Host "Checking insufficient-knowledge handoff..."
$handoffResponse = Invoke-WidgetChat -Message "请告诉我火星仓库里紫色量子烟弹的未公开配方和库存"
if ($handoffResponse.kind -ne "handoff" -or [string]::IsNullOrWhiteSpace($handoffResponse.handoff_id)) {
    throw "Insufficient knowledge did not create a human handoff."
}

Write-Host "WordPress plugin smoke test passed."
Write-Host "Knowledge conversation: $($knowledgeResponse.conversation_id)"
Write-Host "Order boundary conversation: $($orderResponse.conversation_id)"
Write-Host "Handoff: $($handoffResponse.handoff_id)"
