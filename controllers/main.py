from odoo import http, _
from odoo.http import request, Response
from markupsafe import Markup
import json
import logging

_logger = logging.getLogger(__name__)

class Logistics3PLController(http.Controller):

    @http.route('/api/v1/3pl/webhook', type='http', auth='none', methods=['POST'], csrf=False, save_session=False)
    def webhook_3pl_update(self, **kwargs):
        """
        Webhook endpoint to receive status updates and tracking numbers from 3PL.
        
        Expected Headers:
            Authorization: Bearer <your-api-key>
            Content-Type: application/json
        
        Expected Payload:
        {
            "order_id": "WH/OUT/0001",
            "tracking_number": "1Z999AA10123456784",
            "tracking_url": "https://tracking.example.com/1Z999AA10123456784",  (optional)
            "status": "shipped"
        }
        
        Note: When status is "shipped", the picking is auto-validated if in waiting_3pl state.
        If tracking_url is not provided but tracking_number is, the URL will be 
        constructed using the configured Tracking URL Base + tracking_number.
        """
        _logger.info("3PL Webhook: Received request")
        
        # Helper function to return JSON response
        def json_response(data, status=200):
            return Response(
                json.dumps(data),
                status=status,
                content_type='application/json'
            )
        
        # 1. Authentication (Token Check)
        # Use Authorization: Bearer header (same as API calls)
        auth_header = request.httprequest.headers.get('Authorization', '')
        auth_token = None
        if auth_header.startswith('Bearer '):
            auth_token = auth_header[7:]  # Remove 'Bearer ' prefix
        
        _logger.info(f"3PL Webhook: Auth token received: {auth_token[:10]}..." if auth_token else "3PL Webhook: No auth token")
        
        # Use sudo to access config without user context
        stored_key = request.env['ir.config_parameter'].sudo().get_param('logistics_3pl_connector.api_key')
        _logger.info(f"3PL Webhook: Stored key exists: {bool(stored_key)}")
        
        if not stored_key:
            _logger.error("3PL Webhook: API key not configured")
            return json_response({'status': 'error', 'message': '3PL integration not configured on server'}, 500)

        if auth_token != stored_key:
            _logger.warning(f"3PL Webhook: Unauthorized - token mismatch")
            return json_response({'status': 'error', 'message': 'Unauthorized'}, 401)

        try:
            # Get JSON data from request body
            try:
                data = json.loads(request.httprequest.data.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                _logger.error(f"3PL Webhook: Invalid JSON - {e}")
                return json_response({'status': 'error', 'message': 'Invalid JSON body'}, 400)
            
            _logger.info(f"3PL Webhook: Received data: {data}")
            
            if not data:
                return json_response({'status': 'error', 'message': 'Empty JSON body'}, 400)
                 
            order_ref = data.get('order_id')
            tracking_ref = data.get('tracking_number')
            tracking_url = data.get('tracking_url')
            status = data.get('status')

            if not order_ref:
                return json_response({'status': 'error', 'message': 'Missing order_id'}, 400)
            
            # Validate status if provided
            if status:
                allowed_statuses = ['shipped', 'delivered', 'completed', 'error']
                status_lower = status.lower()
                if status_lower not in allowed_statuses:
                    _logger.warning(f"3PL Webhook: Invalid status '{status}' for order {order_ref}. Allowed statuses: {allowed_statuses}")
                    return json_response({
                        'status': 'error',
                        'message': f'Invalid status "{status}". Allowed statuses are: {", ".join(allowed_statuses)}'
                    }, 400)

            # 2. Find the Picking
            picking = request.env['stock.picking'].sudo().search([('name', '=', order_ref)], limit=1)
            _logger.info(f"3PL Webhook: Found picking: {picking.name if picking else 'None'}")
            
            if not picking:
                return json_response({'status': 'error', 'message': f'Order {order_ref} not found'}, 404)

            # 2.1. Validate picking state - only allow updates for pickings in valid states
            allowed_states = ['waiting_3pl', 'assigned']
            if picking.state not in allowed_states:
                _logger.warning(f"3PL Webhook: Rejected update for {order_ref} - picking is in state '{picking.state}', allowed states: {allowed_states}")
                return json_response({
                    'status': 'error', 
                    'message': f'Order {order_ref} is in state "{picking.state}". Updates are only allowed for pickings in states: {", ".join(allowed_states)}'
                }, 400)

            # 3. Update Picking
            # IMPORTANT: Save the current state BEFORE writing updates
            # Because changing x_3pl_status will trigger _compute_state and change the state
            original_state = picking.state
            should_auto_validate = (
                status and 
                status.lower() == 'shipped' and 
                original_state == 'waiting_3pl'
            )
            _logger.info(f"3PL Webhook: {order_ref} - original_state={original_state}, should_auto_validate={should_auto_validate}")
            
            vals = {}
            if tracking_ref:
                vals['x_3pl_tracking_ref'] = tracking_ref
                
                # Build tracking URL if not provided
                if not tracking_url:
                    tracking_url_base = request.env['ir.config_parameter'].sudo().get_param(
                        'logistics_3pl_connector.tracking_url_base', 
                        default='https://tracking.example.com/odoo/'
                    )
                    if tracking_url_base:
                        # Ensure base URL ends with proper separator
                        if not tracking_url_base.endswith('/') and not tracking_url_base.endswith('='):
                            tracking_url_base += '/'
                        tracking_url = f"{tracking_url_base}{tracking_ref}"
            
            if tracking_url:
                vals['x_3pl_tracking_url'] = tracking_url
            
            # Map 3PL status to internal status
            if status:
                status_lower = status.lower()
                if status_lower in ('shipped', 'delivered', 'completed'):
                    vals['x_3pl_status'] = 'shipped'
                elif status_lower == 'error':
                    vals['x_3pl_status'] = 'error'
            
            if vals:
                picking.write(vals)
                
                # Use OdooBot or admin user for message_post since auth='none' has no user
                odoobot = request.env.ref('base.partner_root', raise_if_not_found=False)
                
                # Build message with clickable tracking link if URL available
                status_label = '🚚 Shipped' if status and status.lower() in ('shipped', 'delivered', 'completed') else (status or 'Updated')
                
                if tracking_url and tracking_ref:
                    msg_body = Markup(_("3PL Update: <strong>%s</strong><br/>Tracking: <a href='%s' target='_blank'>%s</a>")) % (status_label, tracking_url, tracking_ref)
                elif tracking_ref:
                    msg_body = _("3PL Update: %s - Tracking: %s") % (status_label, tracking_ref)
                else:
                    msg_body = _("3PL Update: %s") % status_label
                
                picking.with_context(mail_create_nosubscribe=True).message_post(
                    body=msg_body,
                    author_id=odoobot.id if odoobot else False,
                    message_type='notification'
                )
            
            # Auto-validate picking when shipped (if it WAS in waiting_3pl state)
            # We use should_auto_validate which was determined BEFORE writing updates
            if should_auto_validate:
                try:
                    _logger.info(f"3PL Webhook: Attempting to auto-validate picking {order_ref}")
                    
                    # Get user for validation (required because auth='none' has no user context)
                    # Priority: 1) Configured webhook user, 2) OdooBot as fallback
                    # Best practice: Configure a dedicated user with only Inventory permissions
                    webhook_user = None
                    config_param = request.env['ir.config_parameter'].sudo()
                    webhook_user_id = config_param.get_param('logistics_3pl_connector.webhook_user_id')
                    
                    if webhook_user_id:
                        try:
                            webhook_user = request.env['res.users'].sudo().browse(int(webhook_user_id))
                            if not webhook_user.exists() or not webhook_user.active:
                                _logger.warning(f"3PL Webhook: Configured webhook user (id={webhook_user_id}) not found or inactive, falling back to OdooBot")
                                webhook_user = None
                            else:
                                _logger.info(f"3PL Webhook: Using configured webhook user: {webhook_user.name} (id={webhook_user.id})")
                        except (ValueError, TypeError) as e:
                            _logger.warning(f"3PL Webhook: Invalid webhook_user_id config: {e}, falling back to OdooBot")
                    
                    # Fallback to OdooBot if no configured user
                    if not webhook_user:
                        # Get OdooBot user (ID=1) as fallback
                        webhook_user = request.env['res.users'].sudo().browse(1)
                        if webhook_user.exists():
                            _logger.info("3PL Webhook: Using OdooBot (fallback). Consider configuring a dedicated webhook user for better security.")
                        else:
                            webhook_user = None
                    
                    if not webhook_user or not webhook_user.exists():
                        _logger.error("3PL Webhook: Could not find any user for validation")
                        raise Exception("No user available for auto-validation")
                    
                    # IMPORTANT: Refresh picking from database to get current state after write
                    # The write() above triggered _compute_state which changed the state
                    picking = request.env['stock.picking'].with_user(webhook_user).browse(picking.id)
                    picking.ensure_one()
                    _logger.info(f"3PL Webhook: Picking {order_ref} current state after refresh: {picking.state}")
                    
                    # Call button_validate with context flags to:
                    # - skip_3pl_check: bypass our 3PL blocking logic
                    # - skip_3pl_auto_send: prevent recursion
                    # - skip_sms: skip SMS confirmation wizard
                    # - skip_backorder: auto-handle backorders without wizard
                    # - button_validate_picking_ids: required for batch validation
                    validate_ctx = {
                        'skip_3pl_check': True,
                        'skip_3pl_auto_send': True,
                        'skip_sms': True,
                        'skip_backorder': True,
                        'button_validate_picking_ids': picking.ids,
                    }
                    
                    # Validate the picking
                    _logger.info(f"3PL Webhook: Calling button_validate for {order_ref}")
                    result = picking.with_context(**validate_ctx).button_validate()
                    _logger.info(f"3PL Webhook: button_validate returned: {result}")
                    
                    # If result is a wizard action, we need to confirm it
                    if isinstance(result, dict) and result.get('res_model'):
                        wizard_model = result.get('res_model')
                        wizard_id = result.get('res_id')
                        _logger.info(f"3PL Webhook: Wizard returned: {wizard_model} (id={wizard_id}). Attempting to process...")
                        
                        # Try to process the wizard automatically
                        if wizard_id and wizard_model:
                            # Use with_user() to set webhook user context for wizard processing
                            wizard = request.env[wizard_model].with_user(webhook_user).browse(wizard_id)
                            if hasattr(wizard, 'process'):
                                wizard.with_context(**validate_ctx).process()
                                _logger.info(f"3PL Webhook: Wizard {wizard_model} processed")
                            elif hasattr(wizard, 'action_confirm'):
                                wizard.with_context(**validate_ctx).action_confirm()
                                _logger.info(f"3PL Webhook: Wizard {wizard_model} confirmed")
                            elif hasattr(wizard, 'action_done'):
                                wizard.with_context(**validate_ctx).action_done()
                                _logger.info(f"3PL Webhook: Wizard {wizard_model} done")
                    
                    # Re-read picking to get updated state from database
                    picking.invalidate_recordset(['state'])
                    picking = request.env['stock.picking'].with_user(webhook_user).browse(picking.id)
                    _logger.info(f"3PL Webhook: Auto-validated picking {order_ref}. Final state: {picking.state}")
                    if picking.state != 'done':
                        _logger.warning(f"3PL Webhook: Picking {order_ref} validation completed but state is still '{picking.state}', expected 'done'")
                except Exception as validate_error:
                    _logger.error(f"3PL Webhook: Could not auto-validate {order_ref}: {validate_error}", exc_info=True)

            _logger.info(f"3PL Webhook: Successfully updated {order_ref} with tracking {tracking_ref}, URL: {tracking_url}")
            return json_response({'status': 'success', 'order_id': order_ref, 'tracking_url': tracking_url})

        except Exception as e:
            _logger.exception(f"3PL Webhook: Error processing request: {str(e)}")
            return json_response({'status': 'error', 'message': str(e)}, 500)