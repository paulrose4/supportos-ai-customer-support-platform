<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class CPSA_Settings {
	public const OPTION_KEY = 'cpsa_options';

	public static function defaults(): array {
		return array(
			'enabled'         => false,
			'api_base_url'    => '',
			'site_key'        => '',
			'public_widget_id' => '',
			'asset_version'    => '',
			'config_version'   => '',
			'connector_mode'   => 'legacy',
			'presence_mode'   => 'widget_only',
			'widget_title'    => 'Product Support',
			'welcome_message' => 'Hello! Ask me anything about our products.',
			'primary_color'   => '#2563eb',
			'position'        => 'right',
		);
	}

	public static function get_options(): array {
		$options = get_option( self::OPTION_KEY, array() );
		return wp_parse_args( is_array( $options ) ? $options : array(), self::defaults() );
	}

	public function register_hooks(): void {
		add_action( 'admin_menu', array( $this, 'add_settings_page' ) );
		add_action( 'admin_init', array( $this, 'register_settings' ) );
		add_action( 'admin_post_cpsa_test_connection', array( $this, 'test_connection' ) );
	}

	public function add_settings_page(): void {
		add_options_page(
			__( 'Product Support Agent', 'company-product-support-agent' ),
			__( 'Product Support Agent', 'company-product-support-agent' ),
			'manage_options',
			'company-product-support-agent',
			array( $this, 'render_settings_page' )
		);
	}

	public function register_settings(): void {
		register_setting(
			'cpsa_settings_group',
			self::OPTION_KEY,
			array(
				'type'              => 'array',
				'sanitize_callback' => array( $this, 'sanitize_options' ),
				'default'           => self::defaults(),
			)
		);
	}

	public function sanitize_options( $input ): array {
		$input    = is_array( $input ) ? $input : array();
		$defaults = self::defaults();
		$current  = self::get_options();
		$position = isset( $input['position'] ) ? sanitize_key( $input['position'] ) : $current['position'];
		$presence_mode = isset( $input['presence_mode'] ) ? sanitize_key( $input['presence_mode'] ) : $defaults['presence_mode'];
		$color    = isset( $input['primary_color'] ) ? sanitize_hex_color( $input['primary_color'] ) : $current['primary_color'];
		$api_base_url = isset( $input['api_base_url'] ) ? untrailingslashit( esc_url_raw( $input['api_base_url'] ) ) : '';
		$site_key = isset( $input['site_key'] ) ? sanitize_text_field( $input['site_key'] ) : '';
		$credentials_changed = $api_base_url !== $current['api_base_url'] || $site_key !== $current['site_key'];
		$manifest = self::fetch_manifest( $api_base_url, $site_key );
		$public_widget_id = $credentials_changed ? '' : $current['public_widget_id'];
		$asset_version = $credentials_changed ? '' : $current['asset_version'];
		$config_version = $credentials_changed ? '' : $current['config_version'];
		if ( is_array( $manifest ) ) {
			$public_widget_id = $manifest['public_widget_id'];
			$asset_version = $manifest['asset_version'];
			$config_version = $manifest['config_version'];
		}

		return array(
			'enabled'         => ! empty( $input['enabled'] ),
			'api_base_url'    => $api_base_url,
			'site_key'        => $site_key,
			'public_widget_id' => $public_widget_id,
			'asset_version'    => $asset_version,
			'config_version'   => $config_version,
			'connector_mode'   => $public_widget_id ? 'public' : 'legacy',
			'presence_mode'   => in_array( $presence_mode, array( 'page_view', 'widget_only', 'disabled' ), true ) ? $presence_mode : $defaults['presence_mode'],
			'widget_title'    => isset( $input['widget_title'] ) ? sanitize_text_field( $input['widget_title'] ) : $current['widget_title'],
			'welcome_message' => isset( $input['welcome_message'] ) ? sanitize_textarea_field( $input['welcome_message'] ) : $current['welcome_message'],
			'primary_color'   => $color ? $color : $defaults['primary_color'],
			'position'        => in_array( $position, array( 'left', 'right' ), true ) ? $position : 'right',
		);
	}

	public static function fetch_manifest( string $api_base_url, string $site_key ): ?array {
		if ( '' === $api_base_url || strlen( $site_key ) < 32 ) {
			return null;
		}
		$response = wp_remote_get(
			untrailingslashit( $api_base_url ) . '/v1/widget/manifest',
			array(
				'timeout'   => 10,
				'sslverify' => true,
				'headers'   => array(
					'Accept'           => 'application/json',
					'X-Agent-Site-Key' => $site_key,
					'User-Agent'       => 'CompanyProductSupportAgent/' . CPSA_VERSION . '; ' . home_url( '/' ),
				),
			)
		);
		if ( is_wp_error( $response ) || 200 !== wp_remote_retrieve_response_code( $response ) ) {
			return null;
		}
		$payload = json_decode( wp_remote_retrieve_body( $response ), true );
		if ( ! is_array( $payload ) ) {
			return null;
		}
		$public_widget_id = isset( $payload['public_widget_id'] ) ? sanitize_text_field( $payload['public_widget_id'] ) : '';
		$asset_version = isset( $payload['asset_version'] ) ? sanitize_text_field( $payload['asset_version'] ) : '';
		$config_version = isset( $payload['config_version'] ) ? sanitize_text_field( $payload['config_version'] ) : '';
		if (
			1 !== preg_match( '/^site_pub_[A-Za-z0-9_-]{16,64}$/', $public_widget_id ) ||
			1 !== preg_match( '/^[A-Za-z0-9._-]{1,100}$/', $asset_version ) ||
			'' === $config_version || strlen( $config_version ) > 100
		) {
			return null;
		}
		return array(
			'public_widget_id' => $public_widget_id,
			'asset_version'    => $asset_version,
			'config_version'   => $config_version,
		);
	}

	public function test_connection(): void {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( esc_html__( 'You are not allowed to test this connection.', 'company-product-support-agent' ) );
		}
		check_admin_referer( 'cpsa_test_connection' );
		$options = self::get_options();
		$status  = 'not_configured';

		if ( ! empty( $options['api_base_url'] ) && ! empty( $options['site_key'] ) ) {
			$manifest = self::fetch_manifest( $options['api_base_url'], $options['site_key'] );
			if ( is_array( $manifest ) ) {
				$options['public_widget_id'] = $manifest['public_widget_id'];
				$options['asset_version'] = $manifest['asset_version'];
				$options['config_version'] = $manifest['config_version'];
				$options['connector_mode'] = 'public';
				update_option( self::OPTION_KEY, $options, false );
				$status = 'success';
			}
		}

		if ( 'success' !== $status && ! empty( $options['api_base_url'] ) && ! empty( $options['site_key'] ) ) {
			$response = wp_remote_post(
				untrailingslashit( $options['api_base_url'] ) . '/v1/widget/presence',
				array(
					'timeout'   => 10,
					'sslverify' => true,
					'headers'   => array(
						'Content-Type'     => 'application/json',
						'Accept'           => 'application/json',
						'X-Agent-Site-Key' => $options['site_key'],
						'User-Agent'       => 'CompanyProductSupportAgent/' . CPSA_VERSION . '; ' . home_url( '/' ),
					),
					'body'      => wp_json_encode(
						array(
							'visitor_id' => 'diagnostic-' . substr( md5( home_url( '/' ) ), 0, 24 ),
							'page_path'  => '/wordpress-plugin-connection-test',
						)
					),
				)
			);
			if ( is_wp_error( $response ) ) {
				$status = 'unavailable';
			} else {
				$code = wp_remote_retrieve_response_code( $response );
				if ( $code >= 200 && $code < 300 ) {
					$status = 'legacy_success';
				} elseif ( 401 === $code ) {
					$status = 'invalid_key';
				} else {
					$status = 'upstream_error';
				}
			}
		}

		wp_safe_redirect(
			add_query_arg(
				array(
					'page'            => 'company-product-support-agent',
					'cpsa_connection' => $status,
				),
				admin_url( 'options-general.php' )
			)
		);
		exit;
	}

	public function render_settings_page(): void {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		$options           = self::get_options();
		$public_mode      = ! empty( $options['public_widget_id'] );
		$connection_status = isset( $_GET['cpsa_connection'] ) ? sanitize_key( wp_unslash( $_GET['cpsa_connection'] ) ) : '';
		$notices           = array(
			'success'        => array( 'success', __( 'Connection succeeded. This site now uses the Dashboard-published widget configuration.', 'company-product-support-agent' ) ),
			'legacy_success' => array( 'warning', __( 'The legacy proxy is reachable, but public widget identity synchronization is unavailable. Dashboard appearance changes will not apply until the connector is migrated.', 'company-product-support-agent' ) ),
			'invalid_key'    => array( 'error', __( 'The API rejected this site key. Copy the latest key from the support Dashboard.', 'company-product-support-agent' ) ),
			'unavailable'    => array( 'error', __( 'WordPress could not reach the support API. Check DNS, HTTPS and firewall settings.', 'company-product-support-agent' ) ),
			'upstream_error' => array( 'error', __( 'The support API returned an unexpected response. Check its system status and logs.', 'company-product-support-agent' ) ),
			'not_configured' => array( 'warning', __( 'Save an API URL and site key before testing the connection.', 'company-product-support-agent' ) ),
		);
		?>
		<div class="wrap">
			<h1><?php echo esc_html__( 'Company Product Support Agent', 'company-product-support-agent' ); ?></h1>
			<p><?php echo esc_html__( 'Connect this WordPress site to your tenant-specific customer-support agent.', 'company-product-support-agent' ); ?></p>
			<?php if ( isset( $notices[ $connection_status ] ) ) : ?>
				<div class="notice notice-<?php echo esc_attr( $notices[ $connection_status ][0] ); ?> is-dismissible"><p><?php echo esc_html( $notices[ $connection_status ][1] ); ?></p></div>
			<?php endif; ?>
			<form method="post" action="options.php">
				<?php settings_fields( 'cpsa_settings_group' ); ?>
				<table class="form-table" role="presentation">
					<tr>
						<th scope="row"><?php echo esc_html__( 'Enable widget', 'company-product-support-agent' ); ?></th>
						<td><label><input type="checkbox" name="<?php echo esc_attr( self::OPTION_KEY ); ?>[enabled]" value="1" <?php checked( ! empty( $options['enabled'] ) ); ?>> <?php echo esc_html__( 'Display the support widget on the public site', 'company-product-support-agent' ); ?></label></td>
					</tr>
					<tr>
						<th scope="row"><label for="cpsa-api-base-url"><?php echo esc_html__( 'Agent API URL', 'company-product-support-agent' ); ?></label></th>
						<td><input id="cpsa-api-base-url" class="regular-text code" type="url" name="<?php echo esc_attr( self::OPTION_KEY ); ?>[api_base_url]" value="<?php echo esc_attr( $options['api_base_url'] ); ?>" placeholder="https://support-api.example.com" required><p class="description"><?php echo esc_html__( 'Use the public HTTPS base URL of the customer-support agent.', 'company-product-support-agent' ); ?></p></td>
					</tr>
					<tr>
						<th scope="row"><label for="cpsa-site-key"><?php echo esc_html__( 'Site key', 'company-product-support-agent' ); ?></label></th>
						<td><input id="cpsa-site-key" class="regular-text code" type="password" autocomplete="new-password" name="<?php echo esc_attr( self::OPTION_KEY ); ?>[site_key]" value="<?php echo esc_attr( $options['site_key'] ); ?>" required><p class="description"><?php echo esc_html__( 'Stored in WordPress and used only by the server-side proxy. It is never sent to site visitors.', 'company-product-support-agent' ); ?></p></td>
					</tr>
					<tr>
						<th scope="row"><?php echo esc_html__( 'Configuration source', 'company-product-support-agent' ); ?></th>
						<td><?php if ( $public_mode ) : ?><strong><?php echo esc_html__( 'Dashboard published version', 'company-product-support-agent' ); ?></strong><p><code><?php echo esc_html( $options['public_widget_id'] ); ?></code></p><p class="description"><?php echo esc_html( sprintf( __( 'Configuration version: %s. Runtime assets: %s.', 'company-product-support-agent' ), $options['config_version'], $options['asset_version'] ) ); ?></p><?php else : ?><strong><?php echo esc_html__( 'Legacy local proxy', 'company-product-support-agent' ); ?></strong><p class="description"><?php echo esc_html__( 'Run the connection test to synchronize this site with its Dashboard identity.', 'company-product-support-agent' ); ?></p><?php endif; ?></td>
					</tr>
					<?php if ( ! $public_mode ) : ?>
					<tr>
						<th scope="row"><label for="cpsa-widget-title"><?php echo esc_html__( 'Widget title', 'company-product-support-agent' ); ?></label></th>
						<td><input id="cpsa-widget-title" class="regular-text" type="text" name="<?php echo esc_attr( self::OPTION_KEY ); ?>[widget_title]" value="<?php echo esc_attr( $options['widget_title'] ); ?>" maxlength="80"></td>
					</tr>
					<?php endif; ?>
					<tr>
						<th scope="row"><label for="cpsa-presence-mode"><?php echo esc_html__( 'Online visitor tracking', 'company-product-support-agent' ); ?></label></th>
						<td><select id="cpsa-presence-mode" name="<?php echo esc_attr( self::OPTION_KEY ); ?>[presence_mode]"><option value="widget_only" <?php selected( $options['presence_mode'], 'widget_only' ); ?>><?php echo esc_html__( 'Only while the support panel is open', 'company-product-support-agent' ); ?></option><option value="page_view" <?php selected( $options['presence_mode'], 'page_view' ); ?>><?php echo esc_html__( 'While a visitor is viewing the site', 'company-product-support-agent' ); ?></option><option value="disabled" <?php selected( $options['presence_mode'], 'disabled' ); ?>><?php echo esc_html__( 'Disabled', 'company-product-support-agent' ); ?></option></select></td>
					</tr>
					<?php if ( ! $public_mode ) : ?>
					<tr>
						<th scope="row"><label for="cpsa-welcome-message"><?php echo esc_html__( 'Welcome message', 'company-product-support-agent' ); ?></label></th>
						<td><textarea id="cpsa-welcome-message" class="large-text" rows="3" name="<?php echo esc_attr( self::OPTION_KEY ); ?>[welcome_message]" maxlength="500"><?php echo esc_textarea( $options['welcome_message'] ); ?></textarea></td>
					</tr>
					<tr>
						<th scope="row"><label for="cpsa-primary-color"><?php echo esc_html__( 'Primary color', 'company-product-support-agent' ); ?></label></th>
						<td><input id="cpsa-primary-color" type="color" name="<?php echo esc_attr( self::OPTION_KEY ); ?>[primary_color]" value="<?php echo esc_attr( $options['primary_color'] ); ?>"></td>
					</tr>
					<tr>
						<th scope="row"><label for="cpsa-position"><?php echo esc_html__( 'Widget position', 'company-product-support-agent' ); ?></label></th>
						<td><select id="cpsa-position" name="<?php echo esc_attr( self::OPTION_KEY ); ?>[position]"><option value="right" <?php selected( $options['position'], 'right' ); ?>><?php echo esc_html__( 'Bottom right', 'company-product-support-agent' ); ?></option><option value="left" <?php selected( $options['position'], 'left' ); ?>><?php echo esc_html__( 'Bottom left', 'company-product-support-agent' ); ?></option></select></td>
					</tr>
					<?php endif; ?>
				</table>
				<?php submit_button(); ?>
			</form>
			<hr>
			<h2><?php echo esc_html__( 'Connection diagnostics', 'company-product-support-agent' ); ?></h2>
			<p><?php echo esc_html__( 'This test validates API connectivity and the saved site key without calling the AI model.', 'company-product-support-agent' ); ?></p>
			<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
				<input type="hidden" name="action" value="cpsa_test_connection">
				<?php wp_nonce_field( 'cpsa_test_connection' ); ?>
				<?php submit_button( __( 'Test connection', 'company-product-support-agent' ), 'secondary', 'submit', false ); ?>
			</form>
		</div>
		<?php
	}
}
