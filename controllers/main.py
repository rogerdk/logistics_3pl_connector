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
                
                # Auto-validate picking when shipped (if in waiting_3pl state)
                if status and status.lower() == 'shipped' and picking.state == 'waiting_3pl':
                    try:
                        picking.with_context(skip_3pl_check=True).button_validate()
                        _logger.info(f"3PL Webhook: Auto-validated picking {order_ref}")
                    except Exception as validate_error:
                        _logger.warning(f"3PL Webhook: Could not auto-validate {order_ref}: {validate_error}")

            _logger.info(f"3PL Webhook: Successfully updated {order_ref} with tracking {tracking_ref}, URL: {tracking_url}")
            return json_response({'status': 'success', 'order_id': order_ref, 'tracking_url': tracking_url})

        except Exception as e:
            _logger.exception(f"3PL Webhook: Error processing request: {str(e)}")
            return json_response({'status': 'error', 'message': str(e)}, 500)