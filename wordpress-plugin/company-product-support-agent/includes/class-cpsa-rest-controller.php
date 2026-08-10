<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class CPSA_REST_Controller {
	private const NAMESPACE = 'company-product-support-agent/v1';
	private const MAX_CHAT_REQUESTS_PER_MINUTE = 20;
	private const MAX_PRESENCE_REQUESTS_PER_MINUTE = 6;
	private const MAX_PRESENCE_SOURCE_REQUESTS_PER_MINUTE = 120;
	private const MAX_PRESENCE_SITE_REQUESTS_PER_MINUTE = 30000;

	public function register_hooks(): void {
		add_action( 'rest_api_init', array( $this, 'register_routes' ) );
	}

	public function register_routes(): void {
		register_rest_route(
			self::NAMESPACE,
			'/chat',
			array(
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => array( $this, 'chat' ),
				'permission_callback' => '__return_true',
				'args'                => array(
					'message' => array(
						'type'              => 'string',
						'required'          => false,
						'sanitize_callback' => 'sanitize_textarea_field',
						'validate_callback' => static fn( $value ): bool => is_string( $value ) && strlen( trim( $value ) ) > 0 && strlen( $value ) <= 10000,
					),
					'conversation_id' => $this->opaque_id_argument( false ),
					'page_path'       => array(
						'type'              => 'string',
						'required'          => true,
						'sanitize_callback' => 'sanitize_text_field',
						'validate_callback' => static fn( $value ): bool => is_string( $value ) && str_starts_with( $value, '/' ) && ! str_starts_with( $value, '//' ) && strlen( $value ) <= 500,
					),
				),
			)
		);
		register_rest_route(
			self::NAMESPACE,
			'/presence',
			array(
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => array( $this, 'presence' ),
				'permission_callback' => '__return_true',
				'args'                => array(
					'visitor_id'      => $this->opaque_id_argument( true ),
					'conversation_id' => $this->opaque_id_argument( false ),
					'page_path'       => array(
						'type'              => 'string',
						'required'          => true,
						'sanitize_callback' => 'sanitize_text_field',
						'validate_callback' => static fn( $value ): bool => is_string( $value ) && str_starts_with( $value, '/' ) && strlen( $value ) <= 500,
					),
					'page_title'      => $this->optional_text_argument( 200 ),
					'referrer'        => $this->optional_text_argument( 1000 ),
					'language'        => $this->optional_text_argument( 35 ),
					'timezone'        => $this->optional_text_argument( 100 ),
					'page_view_id'    => $this->opaque_id_argument( false ),
					'widget_state'    => array(
						'type' => 'string',
						'required' => false,
						'enum' => array( 'closed', 'open' ),
					),
					'presence_source' => array(
						'type' => 'string',
						'required' => false,
						'enum' => array( 'page_load', 'widget' ),
					),
				),
			)
		);
	}

	public function chat( WP_REST_Request $request ) {
		if ( ! $this->is_same_site_request( $request ) ) {
			return new WP_Error( 'cpsa_invalid_origin', __( 'Invalid request origin.', 'company-product-support-agent' ), array( 'status' => 403 ) );
		}
		if ( ! $this->consume_rate_limit( 'chat', self::MAX_CHAT_REQUESTS_PER_MINUTE ) ) {
			return new WP_Error( 'cpsa_rate_limited', __( 'Too many support requests. Please wait and try again.', 'company-product-support-agent' ), array( 'status' => 429 ) );
		}

		$options = $this->configured_options();
		if ( is_wp_error( $options ) ) {
			return $options;
		}
		$payload = array(
			'message'   => (string) $request->get_param( 'message' ),
			'page_path' => is_string( $request->get_param( 'page_path' ) ) && '' !== $request->get_param( 'page_path' ) ? (string) $request->get_param( 'page_path' ) : '/',
		);
		$conversation_id = $request->get_param( 'conversation_id' );
		if ( is_string( $conversation_id ) && '' !== $conversation_id ) {
			$payload['conversation_id'] = $conversation_id;
		}
		$body = $this->post_to_agent( $options, '/v1/widget/chat', $payload, 25 );
		if ( is_wp_error( $body ) ) {
			return $body;
		}
		return rest_ensure_response(
			array(
				'conversation_id' => isset( $body['conversation_id'] ) ? sanitize_text_field( $body['conversation_id'] ) : '',
				'message'         => isset( $body['message'] ) ? sanitize_textarea_field( $body['message'] ) : '',
				'kind'            => isset( $body['kind'] ) ? sanitize_key( $body['kind'] ) : 'handoff',
				'risk_level'      => isset( $body['risk_level'] ) ? absint( $body['risk_level'] ) : 0,
				'handoff_id'      => isset( $body['handoff_id'] ) && is_string( $body['handoff_id'] ) ? sanitize_text_field( $body['handoff_id'] ) : null,
				'citations'       => isset( $body['citations'] ) && is_array( $body['citations'] ) ? array_values( array_map( 'sanitize_text_field', $body['citations'] ) ) : array(),
				'related_links'   => isset( $body['related_links'] ) && is_array( $body['related_links'] ) ? array_values( array_filter( array_map( 'esc_url_raw', $body['related_links'] ) ) ) : array(),
			)
		);
	}

	public function presence( WP_REST_Request $request ) {
		if ( ! $this->is_same_site_request( $request ) ) {
			return new WP_Error( 'cpsa_invalid_origin', __( 'Invalid request origin.', 'company-product-support-agent' ), array( 'status' => 403 ) );
		}
		$visitor_id = (string) $request->get_param( 'visitor_id' );
		if (
			! $this->consume_rate_limit( 'presence-source', self::MAX_PRESENCE_SOURCE_REQUESTS_PER_MINUTE, $this->visitor_ip_address() ) ||
			! $this->consume_rate_limit( 'presence-visitor', self::MAX_PRESENCE_REQUESTS_PER_MINUTE, $visitor_id ) ||
			! $this->consume_rate_limit( 'presence-site', self::MAX_PRESENCE_SITE_REQUESTS_PER_MINUTE, 'site' )
		) {
			return new WP_Error( 'cpsa_presence_rate_limited', __( 'Presence updates are temporarily limited.', 'company-product-support-agent' ), array( 'status' => 429 ) );
		}
		$options = $this->configured_options();
		if ( is_wp_error( $options ) ) {
			return $options;
		}
		$payload = array(
			'visitor_id' => $visitor_id,
			'page_path'  => (string) $request->get_param( 'page_path' ),
		);
		$conversation_id = $request->get_param( 'conversation_id' );
		if ( is_string( $conversation_id ) && '' !== $conversation_id ) {
			$payload['conversation_id'] = $conversation_id;
		}
		foreach ( array( 'page_title', 'referrer', 'language', 'timezone', 'page_view_id', 'widget_state', 'presence_source' ) as $field ) {
			$value = $request->get_param( $field );
			if ( is_string( $value ) && '' !== $value ) {
				$payload[ $field ] = $value;
			}
		}
		$body = $this->post_to_agent( $options, '/v1/widget/presence', $payload, 8 );
		if ( is_wp_error( $body ) ) {
			return $body;
		}
		return rest_ensure_response( array( 'status' => 'ok' ) );
	}

	private function configured_options() {
		$options = CPSA_Settings::get_options();
		if ( empty( $options['enabled'] ) || empty( $options['api_base_url'] ) || empty( $options['site_key'] ) ) {
			return new WP_Error( 'cpsa_not_configured', __( 'The support widget is not configured.', 'company-product-support-agent' ), array( 'status' => 503 ) );
		}
		return $options;
	}

	private function post_to_agent( array $options, string $path, array $payload, int $timeout ) {
		$response = wp_remote_post(
			untrailingslashit( $options['api_base_url'] ) . $path,
			array(
				'timeout'   => $timeout,
				'sslverify' => true,
				'headers'   => array(
					'Content-Type'     => 'application/json',
					'Accept'           => 'application/json',
					'X-Agent-Site-Key' => $options['site_key'],
					'X-Agent-Visitor-IP' => $this->visitor_ip_address(),
					'X-Agent-Visitor-Country' => $this->visitor_country_code(),
					'X-Agent-Visitor-User-Agent' => $this->visitor_user_agent(),
					'User-Agent'       => 'CompanyProductSupportAgent/' . CPSA_VERSION . '; ' . home_url( '/' ),
				),
				'body'      => wp_json_encode( $payload ),
			)
		);
		if ( is_wp_error( $response ) ) {
			return new WP_Error( 'cpsa_upstream_unavailable', __( 'Support is temporarily unavailable.', 'company-product-support-agent' ), array( 'status' => 502 ) );
		}
		$status_code = wp_remote_retrieve_response_code( $response );
		$body        = json_decode( wp_remote_retrieve_body( $response ), true );
		if ( $status_code < 200 || $status_code >= 300 || ! is_array( $body ) ) {
			return new WP_Error( 'cpsa_upstream_error', __( 'Support is temporarily unavailable.', 'company-product-support-agent' ), array( 'status' => 502 ) );
		}
		return $body;
	}

	private function visitor_ip_address(): string {
		$value = isset( $_SERVER['HTTP_CF_CONNECTING_IP'] )
			? wp_unslash( $_SERVER['HTTP_CF_CONNECTING_IP'] )
			: ( isset( $_SERVER['REMOTE_ADDR'] ) ? wp_unslash( $_SERVER['REMOTE_ADDR'] ) : '' );
		return sanitize_text_field( $value );
	}

	private function visitor_country_code(): string {
		$value = isset( $_SERVER['HTTP_CF_IPCOUNTRY'] ) ? wp_unslash( $_SERVER['HTTP_CF_IPCOUNTRY'] ) : '';
		return sanitize_text_field( $value );
	}

	private function visitor_user_agent(): string {
		$value = isset( $_SERVER['HTTP_USER_AGENT'] ) ? wp_unslash( $_SERVER['HTTP_USER_AGENT'] ) : '';
		return sanitize_text_field( $value );
	}

	private function opaque_id_argument( bool $required ): array {
		return array(
			'type'              => 'string',
			'required'          => $required,
			'sanitize_callback' => 'sanitize_text_field',
			'validate_callback' => static fn( $value ): bool => is_string( $value ) && strlen( $value ) <= 100 && 1 === preg_match( '/^[A-Za-z0-9._:-]+$/', $value ),
		);
	}

	private function optional_text_argument( int $maximum_length ): array {
		return array(
			'type'              => 'string',
			'required'          => false,
			'sanitize_callback' => 'sanitize_text_field',
			'validate_callback' => static fn( $value ): bool => is_string( $value ) && strlen( $value ) <= $maximum_length,
		);
	}

	private function consume_rate_limit( string $bucket, int $limit, string $discriminator = '' ): bool {
		$address = isset( $_SERVER['REMOTE_ADDR'] ) ? sanitize_text_field( wp_unslash( $_SERVER['REMOTE_ADDR'] ) ) : 'unknown';
		$identity = '' !== $discriminator ? $discriminator : $address;
		$key     = 'cpsa_rate_' . $bucket . '_' . substr( hash_hmac( 'sha256', $identity, wp_salt( 'auth' ) ), 0, 32 );
		$count   = (int) get_transient( $key );
		if ( $count >= $limit ) {
			return false;
		}
		set_transient( $key, $count + 1, MINUTE_IN_SECONDS );
		return true;
	}

	private function is_same_site_request( WP_REST_Request $request ): bool {
		$origin = $request->get_header( 'origin' );
		if ( ! $origin ) {
			return true;
		}
		$origin_host = wp_parse_url( $origin, PHP_URL_HOST );
		$site_host   = wp_parse_url( home_url( '/' ), PHP_URL_HOST );
		return is_string( $origin_host ) && is_string( $site_host ) && strtolower( $origin_host ) === strtolower( $site_host );
	}
}
