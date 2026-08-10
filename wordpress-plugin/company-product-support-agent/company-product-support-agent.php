<?php
/**
 * Plugin Name: Company Product Support Agent
 * Plugin URI: https://example.com/company-product-support-agent
 * Description: Adds a tenant-aware, product-customized customer-support AI widget backed by your company agent API.
 * Version: 0.4.0
 * Requires at least: 6.5
 * Requires PHP: 8.0
 * Author: Your Company
 * Text Domain: company-product-support-agent
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'CPSA_VERSION', '0.4.0' );
define( 'CPSA_PLUGIN_FILE', __FILE__ );
define( 'CPSA_PLUGIN_DIR', plugin_dir_path( __FILE__ ) );
define( 'CPSA_PLUGIN_URL', plugin_dir_url( __FILE__ ) );

require_once CPSA_PLUGIN_DIR . 'includes/class-cpsa-settings.php';
require_once CPSA_PLUGIN_DIR . 'includes/class-cpsa-rest-controller.php';

final class CPSA_Plugin {
	private CPSA_Settings $settings;
	private CPSA_REST_Controller $rest_controller;

	public function __construct() {
		$this->settings        = new CPSA_Settings();
		$this->rest_controller = new CPSA_REST_Controller();
	}

	public function init(): void {
		$this->settings->register_hooks();
		$this->rest_controller->register_hooks();
		add_action( 'wp_enqueue_scripts', array( $this, 'enqueue_widget_assets' ) );
		add_filter( 'script_loader_tag', array( $this, 'filter_widget_script_tag' ), 10, 3 );
	}

	public static function activate(): void {
		$current = get_option( CPSA_Settings::OPTION_KEY, array() );
		if ( ! is_array( $current ) ) {
			$current = array();
		}
		update_option(
			CPSA_Settings::OPTION_KEY,
			wp_parse_args( $current, CPSA_Settings::defaults() ),
			false
		);
	}

	public function enqueue_widget_assets(): void {
		$options = CPSA_Settings::get_options();
		if ( empty( $options['enabled'] ) || empty( $options['api_base_url'] ) ) {
			return;
		}
		$public_mode = ! empty( $options['public_widget_id'] );
		if ( ! $public_mode && empty( $options['site_key'] ) ) {
			return;
		}
		$asset_version = ! empty( $options['asset_version'] ) ? $options['asset_version'] : CPSA_VERSION;
		$script_url    = $public_mode
			? untrailingslashit( $options['api_base_url'] ) . '/widget.js?v=' . rawurlencode( $asset_version )
			: CPSA_PLUGIN_URL . 'public/js/widget.js';

		wp_enqueue_script(
			'cpsa-widget',
			$script_url,
			array(),
			CPSA_VERSION,
			array(
				'in_footer' => true,
				'strategy'  => 'defer',
			)
		);
		if ( $public_mode ) {
			return;
		}

		$config = array(
			'endpoint'          => esc_url_raw( rest_url( 'company-product-support-agent/v1/chat' ) ),
			'presenceEndpoint'  => esc_url_raw( rest_url( 'company-product-support-agent/v1/presence' ) ),
			'presenceMode'      => $options['presence_mode'],
			'siteId'            => md5( home_url( '/' ) ),
			'title'          => $options['widget_title'],
			'welcomeMessage' => $options['welcome_message'],
			'primaryColor'   => $options['primary_color'],
			'position'       => $options['position'],
			'storageKey'        => 'cpsa_conversation_' . md5( home_url( '/' ) ),
			'visitorStorageKey' => 'cpsa_visitor_' . md5( home_url( '/' ) ),
			'labels'         => array(
				'open'        => __( 'Open customer support', 'company-product-support-agent' ),
				'close'       => __( 'Close customer support', 'company-product-support-agent' ),
				'placeholder' => __( 'Type your question…', 'company-product-support-agent' ),
				'send'        => __( 'Send', 'company-product-support-agent' ),
				'clear'       => __( 'New conversation', 'company-product-support-agent' ),
				'error'       => __( 'Support is temporarily unavailable. Please try again later.', 'company-product-support-agent' ),
				'citations'   => __( 'Sources', 'company-product-support-agent' ),
			),
		);
		wp_add_inline_script(
			'cpsa-widget',
			'window.CPSAWidgetConfig = ' . wp_json_encode( $config ) . ';',
			'before'
		);
	}

	public function filter_widget_script_tag( string $tag, string $handle, string $src ): string {
		if ( 'cpsa-widget' !== $handle ) {
			return $tag;
		}
		$options = CPSA_Settings::get_options();
		if ( empty( $options['public_widget_id'] ) ) {
			return $tag;
		}
		$processor = new WP_HTML_Tag_Processor( $tag );
		if ( ! $processor->next_tag( 'script' ) ) {
			return $tag;
		}
		$processor->set_attribute( 'data-site-id', $options['public_widget_id'] );
		$processor->set_attribute( 'data-presence-mode', $options['presence_mode'] );
		$processor->set_attribute( 'data-connector-type', 'wordpress' );
		$processor->set_attribute( 'data-runtime-version', CPSA_VERSION );
		$processor->set_attribute( 'data-cfasync', 'false' );
		return $processor->get_updated_html();
	}
}

register_activation_hook( __FILE__, array( 'CPSA_Plugin', 'activate' ) );

add_action(
	'plugins_loaded',
	static function (): void {
		$plugin = new CPSA_Plugin();
		$plugin->init();
	}
);


