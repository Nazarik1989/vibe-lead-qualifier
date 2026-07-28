[CmdletBinding()]
param(
    [uri]$ApiBaseUrl = "http://127.0.0.1:8000"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($ApiBaseUrl.Scheme -notin @("http", "https") -or -not $ApiBaseUrl.IsLoopback) {
    throw "Demo-скрипт принимает только loopback HTTP(S) URL."
}

$BaseUrl = $ApiBaseUrl.AbsoluteUri.TrimEnd("/")
$DialogId = "demo-$([guid]::NewGuid().ToString('N'))"

function ConvertFrom-Utf8JsonResponse {
    param(
        [Parameter(Mandatory)]
        $WebResponse
    )

    $ResponseBytes = $WebResponse.RawContentStream.ToArray()
    $ResponseText = [System.Text.Encoding]::UTF8.GetString($ResponseBytes)
    $ResponseText | ConvertFrom-Json
}

function Invoke-DemoMessage {
    param(
        [Parameter(Mandatory)]
        [string]$MessageId,
        [Parameter(Mandatory)]
        [string]$Text
    )

    $Body = [ordered]@{
        dialog_id = $DialogId
        message_id = $MessageId
        text = $Text
        channel = "demo"
        context = [ordered]@{
            source = "scripts/demo.ps1"
        }
    } | ConvertTo-Json -Depth 6

    $WebResponse = Invoke-WebRequest `
        -Method Post `
        -Uri "$BaseUrl/demo/messages" `
        -UseBasicParsing `
        -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($Body))

    ConvertFrom-Utf8JsonResponse -WebResponse $WebResponse
}

Write-Host "Проверка приложения: $BaseUrl/health"
$HealthResponse = Invoke-WebRequest -Method Get -Uri "$BaseUrl/health" -UseBasicParsing
$Health = ConvertFrom-Utf8JsonResponse -WebResponse $HealthResponse
$Health | ConvertTo-Json -Depth 8

$Messages = @(
    "Меня зовут Анна. Нужен лендинг для онлайн-школы.",
    "Бюджет 120 000 рублей, готовность до 15 августа.",
    "Контакт: +7 999 123-45-67; комментарий: интеграция с CRM обязательна."
)

for ($Index = 0; $Index -lt $Messages.Count; $Index++) {
    $Number = $Index + 1
    Write-Host "`nСообщение ${Number}: $($Messages[$Index])"
    $Response = Invoke-DemoMessage -MessageId "$DialogId-message-$Number" -Text $Messages[$Index]
    $Response | ConvertTo-Json -Depth 12
}

Write-Host "`nИтоговое состояние диалога $DialogId"
$DialogResponse = Invoke-WebRequest `
    -Method Get `
    -Uri "$BaseUrl/demo/dialogs/$DialogId" `
    -UseBasicParsing
$Dialog = ConvertFrom-Utf8JsonResponse -WebResponse $DialogResponse
$Dialog | ConvertTo-Json -Depth 12
