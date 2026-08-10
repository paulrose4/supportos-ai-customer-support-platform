<?php

declare(strict_types=1);

if (!defined('CPSA_CONNECTOR_BOOTSTRAPPED')) {
    http_response_code(404);
    exit;
}

return [
    'agent_api_base_url' => 'https://support-api.example.com',
    'site_key' => 'replace-with-at-least-32-random-characters',
    'public_origin' => 'https://www.example.com',
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
