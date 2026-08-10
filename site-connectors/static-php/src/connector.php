<?php

declare(strict_types=1);

define('CPSA_CONNECTOR_BOOTSTRAPPED', true);

function cpsa_handle_request(string $operation): void
{
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        header('Allow: POST');
        cpsa_json_error(405, 'method_not_allowed', 'Only POST requests are accepted.');
    }

    $config = cpsa_load_config();
    cpsa_require_same_origin($config);
    $payload = cpsa_read_json_body();

    if ($operation === 'chat') {
        $validated = cpsa_validate_chat_payload($payload);
        cpsa_consume_rate_limit($operation, $config);
        $response = cpsa_post_to_agent($config, '/v1/widget/chat', $validated, (int) $config['request_timeout_seconds']);
        cpsa_json_response(200, cpsa_public_chat_response($response));
    }
    if ($operation === 'presence') {
        $validated = cpsa_validate_presence_payload($payload);
        cpsa_consume_rate_limit($operation, $config, $validated['visitor_id']);
        cpsa_post_to_agent($config, '/v1/widget/presence', $validated, (int) $config['presence_timeout_seconds']);
        cpsa_json_response(200, ['status' => 'ok']);
    }
    if ($operation === 'messages') {
        $validated = cpsa_validate_messages_payload($payload);
        cpsa_consume_rate_limit($operation, $config);
        $response = cpsa_post_to_agent($config, '/v1/widget/messages', $validated, (int) $config['presence_timeout_seconds']);
        cpsa_json_response(200, cpsa_public_human_messages_response($response));
    }

    cpsa_json_error(404, 'unsupported_operation', 'Unsupported connector operation.');
}

function cpsa_load_config(): array
{
    $configuredPath = getenv('CPSA_CONFIG_PATH');
    $configPath = is_string($configuredPath) && $configuredPath !== ''
        ? $configuredPath
        : dirname(__DIR__) . '/private/config.php';
    if (!is_file($configPath)) {
        cpsa_json_error(503, 'connector_not_configured', 'Support is temporarily unavailable.');
    }
    $config = require $configPath;
    if (!is_array($config)) {
        cpsa_json_error(503, 'connector_not_configured', 'Support is temporarily unavailable.');
    }
    $defaults = [
        'connect_timeout_seconds' => 5,
        'request_timeout_seconds' => 30,
        'presence_timeout_seconds' => 8,
        'chat_rate_limit_per_minute' => 20,
        'presence_rate_limit_per_minute' => 6,
        'presence_source_rate_limit_per_minute' => 120,
        'presence_site_rate_limit_per_minute' => 30000,
        'allow_missing_origin' => false,
        'allow_insecure_agent_api' => false,
    ];
    $config = array_merge($defaults, $config);
    foreach (['agent_api_base_url', 'site_key', 'public_origin'] as $required) {
        if (!isset($config[$required]) || !is_string($config[$required]) || trim($config[$required]) === '') {
            cpsa_json_error(503, 'connector_not_configured', 'Support is temporarily unavailable.');
        }
    }
    $apiScheme = parse_url($config['agent_api_base_url'], PHP_URL_SCHEME);
    if ($apiScheme !== 'https' && !($apiScheme === 'http' && $config['allow_insecure_agent_api'] === true)) {
        cpsa_json_error(503, 'connector_not_configured', 'Support is temporarily unavailable.');
    }
    if (cpsa_normalize_origin($config['public_origin']) === null) {
        cpsa_json_error(503, 'connector_not_configured', 'Support is temporarily unavailable.');
    }
    if (strlen($config['site_key']) < 32) {
        cpsa_json_error(503, 'connector_not_configured', 'Support is temporarily unavailable.');
    }
    return $config;
}

function cpsa_require_same_origin(array $config): void
{
    $expected = cpsa_normalize_origin($config['public_origin']);
    $origin = isset($_SERVER['HTTP_ORIGIN']) ? cpsa_normalize_origin((string) $_SERVER['HTTP_ORIGIN']) : null;
    if ($origin === null && $config['allow_missing_origin'] === true) {
        return;
    }
    if ($origin === null || !hash_equals((string) $expected, $origin)) {
        cpsa_json_error(403, 'invalid_origin', 'Invalid request origin.');
    }
}

function cpsa_normalize_origin(string $value): ?string
{
    $parts = parse_url(trim($value));
    if (!is_array($parts) || !isset($parts['scheme'], $parts['host'])) {
        return null;
    }
    $scheme = strtolower((string) $parts['scheme']);
    if ($scheme !== 'http' && $scheme !== 'https') {
        return null;
    }
    $origin = $scheme . '://' . strtolower((string) $parts['host']);
    if (isset($parts['port'])) {
        $origin .= ':' . (int) $parts['port'];
    }
    return $origin;
}

function cpsa_consume_rate_limit(string $bucket, array $config, ?string $visitorId = null): void
{
    if ($bucket === 'presence' && $visitorId !== null) {
        cpsa_consume_bucket_rate_limit('presence-visitor', $visitorId, (int) $config['presence_rate_limit_per_minute'], $config);
        $address = (string) ($_SERVER['HTTP_CF_CONNECTING_IP'] ?? $_SERVER['REMOTE_ADDR'] ?? 'unknown');
        cpsa_consume_bucket_rate_limit('presence-source', $address, (int) $config['presence_source_rate_limit_per_minute'], $config);
        cpsa_consume_bucket_rate_limit('presence-site', 'site', (int) $config['presence_site_rate_limit_per_minute'], $config);
        return;
    }
    $limitKey = $bucket === 'presence' ? 'presence_rate_limit_per_minute' : 'chat_rate_limit_per_minute';
    $address = (string) ($_SERVER['REMOTE_ADDR'] ?? 'unknown');
    cpsa_consume_bucket_rate_limit($bucket, $address, (int) $config[$limitKey], $config);
}

function cpsa_consume_bucket_rate_limit(string $bucket, string $identity, int $configuredLimit, array $config): void
{
    $limit = max(1, $configuredLimit);
    $fingerprint = hash_hmac('sha256', $bucket . '|' . $identity, $config['site_key']);
    $path = rtrim(sys_get_temp_dir(), DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . 'cpsa-rate-' . substr($fingerprint, 0, 32) . '.json';
    $handle = fopen($path, 'c+');
    if ($handle === false || !flock($handle, LOCK_EX)) {
        if (is_resource($handle)) {
            fclose($handle);
        }
        cpsa_json_error(503, 'rate_limit_unavailable', 'Support is temporarily unavailable.');
    }
    $window = intdiv(time(), 60);
    $contents = stream_get_contents($handle);
    $state = is_string($contents) && $contents !== '' ? json_decode($contents, true) : null;
    $count = is_array($state) && ($state['window'] ?? null) === $window ? (int) ($state['count'] ?? 0) : 0;
    if ($count >= $limit) {
        flock($handle, LOCK_UN);
        fclose($handle);
        cpsa_json_error(429, 'rate_limited', 'Too many support requests. Please wait and try again.');
    }
    rewind($handle);
    ftruncate($handle, 0);
    fwrite($handle, json_encode(['window' => $window, 'count' => $count + 1], JSON_THROW_ON_ERROR));
    fflush($handle);
    flock($handle, LOCK_UN);
    fclose($handle);
}

function cpsa_read_json_body(): array
{
    $contentLength = isset($_SERVER['CONTENT_LENGTH']) ? (int) $_SERVER['CONTENT_LENGTH'] : 0;
    if ($contentLength > 16384) {
        cpsa_json_error(413, 'payload_too_large', 'Request body is too large.');
    }
    $raw = file_get_contents('php://input', false, null, 0, 16385);
    if (!is_string($raw) || $raw === '' || strlen($raw) > 16384) {
        cpsa_json_error(400, 'invalid_json', 'A JSON request body is required.');
    }
    try {
        $payload = json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
    } catch (JsonException $error) {
        cpsa_json_error(400, 'invalid_json', 'A valid JSON request body is required.');
    }
    if (!is_array($payload)) {
        cpsa_json_error(400, 'invalid_json', 'A JSON object is required.');
    }
    return $payload;
}

function cpsa_validate_chat_payload(array $payload): array
{
    $message = isset($payload['message']) && is_string($payload['message']) ? trim($payload['message']) : '';
    if ($message === '' || strlen($message) > 10000) {
        cpsa_json_error(422, 'invalid_message', 'Message must contain between 1 and 10000 characters.');
    }
    $validated = ['message' => $message];
    $pagePath = isset($payload['page_path']) && is_string($payload['page_path']) ? trim($payload['page_path']) : '/';
    if ($pagePath === '' || !str_starts_with($pagePath, '/') || str_starts_with($pagePath, '//') || strlen($pagePath) > 500) {
        cpsa_json_error(422, 'invalid_page_path', 'Page path must be a relative path.');
    }
    $validated['page_path'] = $pagePath;
    if (isset($payload['conversation_id']) && $payload['conversation_id'] !== '') {
        $validated['conversation_id'] = cpsa_validate_opaque_id($payload['conversation_id'], 'conversation_id');
    }
    return $validated;
}

function cpsa_validate_presence_payload(array $payload): array
{
    $pagePath = isset($payload['page_path']) && is_string($payload['page_path']) ? trim($payload['page_path']) : '';
    if ($pagePath === '' || $pagePath[0] !== '/' || strlen($pagePath) > 500) {
        cpsa_json_error(422, 'invalid_page_path', 'Page path must be a relative path.');
    }
    $validated = [
        'visitor_id' => cpsa_validate_opaque_id($payload['visitor_id'] ?? null, 'visitor_id'),
        'page_path' => $pagePath,
    ];
    if (isset($payload['conversation_id']) && $payload['conversation_id'] !== '') {
        $validated['conversation_id'] = cpsa_validate_opaque_id($payload['conversation_id'], 'conversation_id');
    }
    if (isset($payload['page_view_id']) && $payload['page_view_id'] !== '') {
        $validated['page_view_id'] = cpsa_validate_opaque_id($payload['page_view_id'], 'page_view_id');
    }
    foreach (['widget_state' => ['closed', 'open'], 'presence_source' => ['page_load', 'widget']] as $field => $allowed) {
        if (isset($payload[$field])) {
            if (!is_string($payload[$field]) || !in_array($payload[$field], $allowed, true)) {
                cpsa_json_error(422, 'invalid_' . $field, $field . ' is invalid.');
            }
            $validated[$field] = $payload[$field];
        }
    }
    foreach (['page_title' => 200, 'referrer' => 1000, 'language' => 35, 'timezone' => 100] as $field => $maximumLength) {
        if (isset($payload[$field]) && is_string($payload[$field])) {
            $validated[$field] = substr(trim($payload[$field]), 0, $maximumLength);
        }
    }
    return $validated;
}

function cpsa_validate_messages_payload(array $payload): array
{
    return [
        'conversation_id' => cpsa_validate_opaque_id($payload['conversation_id'] ?? null, 'conversation_id'),
    ];
}

function cpsa_validate_opaque_id(mixed $value, string $field): string
{
    if (!is_string($value) || preg_match('/^[A-Za-z0-9._:-]{1,100}$/', $value) !== 1) {
        cpsa_json_error(422, 'invalid_' . $field, $field . ' must be an opaque identifier.');
    }
    return $value;
}

function cpsa_post_to_agent(array $config, string $path, array $payload, int $timeout): array
{
    if (!function_exists('curl_init')) {
        cpsa_json_error(503, 'connector_dependency_missing', 'Support is temporarily unavailable.');
    }
    $handle = curl_init(rtrim($config['agent_api_base_url'], '/') . $path);
    if ($handle === false) {
        cpsa_json_error(502, 'upstream_unavailable', 'Support is temporarily unavailable.');
    }
    $visitorAddress = (string) ($_SERVER['HTTP_CF_CONNECTING_IP'] ?? $_SERVER['REMOTE_ADDR'] ?? '');
    $visitorCountry = preg_match('/^[A-Za-z]{2}$/', (string) ($_SERVER['HTTP_CF_IPCOUNTRY'] ?? '')) === 1
        ? strtoupper((string) $_SERVER['HTTP_CF_IPCOUNTRY'])
        : '';
    $visitorUserAgent = substr(str_replace(["\r", "\n"], '', (string) ($_SERVER['HTTP_USER_AGENT'] ?? '')), 0, 500);
    $options = [
        CURLOPT_POST => true,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => max(1, (int) $config['connect_timeout_seconds']),
        CURLOPT_TIMEOUT => max(1, $timeout),
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_HTTPHEADER => [
            'Accept: application/json',
            'Content-Type: application/json',
            'X-Agent-Site-Key: ' . $config['site_key'],
            'X-Agent-Visitor-IP: ' . (filter_var($visitorAddress, FILTER_VALIDATE_IP) !== false ? $visitorAddress : ''),
            'X-Agent-Visitor-Country: ' . $visitorCountry,
            'X-Agent-Visitor-User-Agent: ' . $visitorUserAgent,
            'User-Agent: CompanyProductSupportAgentStaticConnector/0.1.0',
        ],
        CURLOPT_POSTFIELDS => json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR),
    ];
    if (defined('CURLOPT_PROTOCOLS') && defined('CURLPROTO_HTTP') && defined('CURLPROTO_HTTPS')) {
        $options[CURLOPT_PROTOCOLS] = $config['allow_insecure_agent_api'] === true
            ? CURLPROTO_HTTP | CURLPROTO_HTTPS
            : CURLPROTO_HTTPS;
    }
    curl_setopt_array($handle, $options);
    $raw = curl_exec($handle);
    $status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
    $failed = $raw === false;
    curl_close($handle);
    if ($failed || !is_string($raw)) {
        cpsa_json_error(502, 'upstream_unavailable', 'Support is temporarily unavailable.');
    }
    try {
        $decoded = json_decode($raw, true, 32, JSON_THROW_ON_ERROR);
    } catch (JsonException $error) {
        cpsa_json_error(502, 'upstream_invalid_response', 'Support is temporarily unavailable.');
    }
    if ($status === 429) {
        cpsa_json_error(429, 'upstream_rate_limited', 'Too many support requests. Please wait and try again.');
    }
    if ($status < 200 || $status >= 300 || !is_array($decoded)) {
        cpsa_json_error(502, 'upstream_error', 'Support is temporarily unavailable.');
    }
    return $decoded;
}

function cpsa_public_chat_response(array $response): array
{
    $citations = [];
    if (isset($response['citations']) && is_array($response['citations'])) {
        foreach ($response['citations'] as $citation) {
            if (is_string($citation)) {
                $citations[] = $citation;
            }
        }
    }
    $relatedLinks = [];
    if (isset($response['related_links']) && is_array($response['related_links'])) {
        foreach ($response['related_links'] as $relatedLink) {
            if (is_string($relatedLink) && filter_var($relatedLink, FILTER_VALIDATE_URL) !== false) {
                $relatedLinks[] = $relatedLink;
            }
        }
    }
    return [
        'conversation_id' => isset($response['conversation_id']) && is_string($response['conversation_id']) ? $response['conversation_id'] : '',
        'message' => isset($response['message']) && is_string($response['message']) ? $response['message'] : '',
        'kind' => isset($response['kind']) && is_string($response['kind']) ? $response['kind'] : 'handoff',
        'risk_level' => isset($response['risk_level']) ? (int) $response['risk_level'] : 0,
        'handoff_id' => isset($response['handoff_id']) && is_string($response['handoff_id']) ? $response['handoff_id'] : null,
        'citations' => $citations,
        'related_links' => $relatedLinks,
    ];
}

function cpsa_public_human_messages_response(array $response): array
{
    $items = [];
    if (isset($response['items']) && is_array($response['items'])) {
        foreach ($response['items'] as $item) {
            if (!is_array($item) || !is_string($item['message_id'] ?? null) || !is_string($item['content'] ?? null)) {
                continue;
            }
            $items[] = [
                'message_id' => $item['message_id'],
                'content' => $item['content'],
                'created_at' => is_string($item['created_at'] ?? null) ? $item['created_at'] : '',
            ];
        }
    }
    return ['items' => $items];
}

function cpsa_json_response(int $status, array $payload): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    exit;
}

function cpsa_json_error(int $status, string $code, string $message): void
{
    cpsa_json_response($status, ['error' => ['code' => $code, 'message' => $message]]);
}
