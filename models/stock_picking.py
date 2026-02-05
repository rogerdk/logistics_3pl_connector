import requests
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # Extend native state field with 3PL intermediate state
    # Re-declare 'done' after 'waiting_3pl' to insert it in the correct position
    state = fields.Selection(selection_add=[
        ('waiting_3pl', "Waiting 3PL"),
        ('done',),  # This forces waiting_3pl to appear BEFORE done
    ], ondelete={'waiting_3pl': 'set assigned'})

    x_3pl_order_id = fields.Char(string="3PL Order ID", readonly=True, copy=False)
    x_3pl_status = fields.Selection([
        ('draft', 'Not Sent'),
        ('sent', 'Sent to 3PL'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('error', 'Error'),
    ], string="3PL Status", default='draft', copy=False, tracking=True)
    x_3pl_tracking_ref = fields.Char(string="Tracking Number", readonly=True, copy=False)
    x_3pl_tracking_url = fields.Char(string="3PL Tracking URL", readonly=True, copy=False)
    x_3pl_current_state = fields.Char(string="e-Transport State", readonly=True, copy=False,
        help="Current state reported by e-Transport TMS")
    x_3pl_eligible = fields.Boolean(compute='_compute_3pl_eligible', store=False)
    x_3pl_can_resend = fields.Boolean(compute='_compute_3pl_can_resend', store=False,
        help="True if this picking can be resent to e-Transport (allow_resend enabled and already sent)")
    x_is_web_order = fields.Boolean(
        compute='_compute_is_web_order',
        string="Is Web Order",
        help="True if this picking originates from an eCommerce order"
    )
    
    @api.depends('move_ids.state', 'x_3pl_status')
    def _compute_state(self):
        """
        Override to preserve 'waiting_3pl' state.
        
        The native _compute_state method recalculates the state based on move states,
        which would overwrite our custom 'waiting_3pl' state. We need to:
        1. Let the parent compute the state normally
        2. Then restore 'waiting_3pl' if the picking should be in that state
        
        A picking should be in 'waiting_3pl' state when:
        - x_3pl_status is 'sent' (sent to 3PL, waiting for confirmation)
        - The computed state would otherwise be 'assigned' or 'waiting_3pl'
        - The picking is NOT done or cancelled
        """
        # First, let the parent compute the state
        super()._compute_state()
        
        # Then, restore 'waiting_3pl' for pickings that should be in that state
        for picking in self:
            # If 3PL status is 'sent' and picking is not done/cancelled, it should be waiting_3pl
            if picking.x_3pl_status == 'sent' and picking.state not in ('done', 'cancel'):
                picking.state = 'waiting_3pl'
    
    @api.depends('picking_type_id', 'picking_type_id.warehouse_id')
    def _compute_3pl_eligible(self):
        """Check if picking belongs to the configured 3PL warehouse."""
        config = self.env['ir.config_parameter'].sudo()
        target_warehouse_id = config.get_param('logistics_3pl_connector.warehouse_id')
        
        for picking in self:
            if not target_warehouse_id:
                # No warehouse configured = all outgoing pickings are eligible
                picking.x_3pl_eligible = picking.picking_type_code == 'outgoing'
            else:
                try:
                    target_wh_id = int(target_warehouse_id)
                    picking.x_3pl_eligible = (
                        picking.picking_type_code == 'outgoing' and 
                        picking.picking_type_id.warehouse_id.id == target_wh_id
                    )
                except (ValueError, TypeError):
                    picking.x_3pl_eligible = False
    
    @api.depends('x_3pl_status', 'x_3pl_eligible')
    def _compute_3pl_can_resend(self):
        """
        Check if picking can be resent to e-Transport.
        Requires:
        - allow_resend configuration enabled
        - picking is 3PL eligible
        - picking has already been sent (status is 'sent', 'shipped', or 'delivered')
        """
        config = self.env['ir.config_parameter'].sudo()
        allow_resend = config.get_param('logistics_3pl_connector.allow_resend', 'False').lower() == 'true'
        
        for picking in self:
            picking.x_3pl_can_resend = (
                allow_resend and 
                picking.x_3pl_eligible and 
                picking.x_3pl_status in ('sent', 'shipped', 'delivered')
            )
    
    @api.depends('sale_id')
    def _compute_is_web_order(self):
        """Check if picking comes from a website order.
        
        Note: website_id is NOT in @depends because it's an optional field
        that only exists when website_sale module is installed. We check
        for it defensively inside the method.
        """
        # Check if website_id field exists in sale.order (website_sale installed)
        has_website_field = 'website_id' in self.env['sale.order']._fields
        
        for picking in self:
            is_web = False
            if picking.sale_id and has_website_field:
                # Access website_id only if the field exists
                if picking.sale_id.website_id:
                    is_web = True
            picking.x_is_web_order = is_web
    
    def action_open_3pl_tracking(self):
        """Open the tracking URL in a new browser tab."""
        self.ensure_one()
        if not self.x_3pl_tracking_url:
            raise UserError(_("No tracking URL available for this delivery."))
        return {
            'type': 'ir.actions.act_url',
            'url': self.x_3pl_tracking_url,
            'target': 'new',
        }
    
    @staticmethod
    def _format_time_slot(float_time):
        """Convert float time to HH:MM string format"""
        hours = int(float_time)
        minutes = int((float_time - hours) * 60)
        return f"{hours:02d}:{minutes:02d}"
    
    def _get_etransport_shipment_type(self):
        """Determine ShipmentType based on order origin"""
        config = self.env['ir.config_parameter'].sudo()
        
        # If it's a web/eCommerce order → use eCommerce ShipmentType (E)
        if self.x_is_web_order:
            return config.get_param('logistics_3pl_connector.shipment_type', 'E')
        
        # If it's a manual/internal order → use Internal ShipmentType (M)
        return config.get_param('logistics_3pl_connector.shipment_type_internal', 'M')
    
    def _get_contact_info(self):
        """
        Get contact phone and email for the delivery.
        Always uses partner's (customer) contact info. If not available, returns empty strings.
        """
        partner = self.partner_id
        
        # Get phone: try partner.phone, then partner.mobile (if exists)
        phone = partner.phone or getattr(partner, 'mobile', '') or ''
        email = partner.email or ''
        
        return phone, email
    
    def _prepare_etransport_payload(self):
        """Build payload for e-Transport TMS API"""
        self.ensure_one()
        config = self.env['ir.config_parameter'].sudo()
        
        # Get config values
        shipment_type = self._get_etransport_shipment_type()
        service_type = config.get_param('logistics_3pl_connector.service_type', 'ND_3H')
        default_temp = config.get_param('logistics_3pl_connector.default_temperature', 'FR')
        
        partner = self.partner_id
        
        # Get contact info (uses user info for internal orders)
        contact_phone, contact_email = self._get_contact_info()
        
        # Build Goods - one line per move
        goods = []
        for move in self.move_ids.filtered(lambda m: m.state != 'cancel'):
            product = move.product_id
            qty = move.product_uom_qty
            
            # PacksTypeID: use default_code, or generate from product name
            packs_type_id = product.default_code
            if not packs_type_id:
                name = (product.name or 'generic').lower()
                if '-' in name:
                    # Has hyphen: just remove spaces
                    packs_type_id = name.replace(' ', '')
                else:
                    # No hyphen: replace spaces with hyphens
                    # Use split/join to handle multiple spaces
                    packs_type_id = '-'.join(name.split())
            
            good = {
                'Packs': int(qty),
                'PacksTypeID': packs_type_id,
                'PacksDescription': product.name,
                'PacksTemperature': default_temp,
                'GrossWeight': round((product.weight or 0) * qty, 2),
                'Parcels': []
            }
            # Cube only if product has volume
            if product.volume:
                good['Cube'] = round(product.volume * qty, 3)
            
            goods.append(good)
        
        # Build Leg (delivery destination)
        leg = {
            'UnLoadName': partner.name or '',
            'UnLoadAddress': partner.street or '',
            'UnLoadCity': partner.city or '',
            'UnLoadZip': partner.zip or '',
            'UnLoadCountry': partner.country_id.code if partner.country_id else 'ES',
            'UnLoadTel': contact_phone,
            'UnLoadEmail': contact_email,
            'Goods': goods,
        }
        
        # Add delivery date/time from delivery_time_slots module if available
        if hasattr(self, 'scheduled_delivery_date') and self.scheduled_delivery_date:
            leg['UnLoadDate'] = self.scheduled_delivery_date.strftime('%Y-%m-%d')
        
        if hasattr(self, 'delivery_time_slot_id') and self.delivery_time_slot_id:
            slot = self.delivery_time_slot_id
            if slot.exists() and hasattr(slot, 'start_hour') and hasattr(slot, 'end_hour'):
                if slot.start_hour is not None and slot.end_hour is not None:
                    leg['UnLoadStartTime'] = self._format_time_slot(slot.start_hour)
                    leg['UnLoadEndTime'] = self._format_time_slot(slot.end_hour)
        
        # Build Order
        order = {
            'ExternalRef': self.name,
            'ShipmentType': shipment_type,
            'ServiceType': service_type,
            'Legs': [leg]
        }
        
        return {'Orders': [order]}

    def action_send_to_3pl(self):
        """
        Send picking to e-Transport TMS. Changes state to 'waiting_3pl' on success.
        
        Handles three scenarios:
        - Initial send (status is 'draft')
        - Retry after error (status is 'error') 
        - Resend (status is 'sent', 'shipped', 'delivered') - requires allow_resend config
        """
        self.ensure_one()
        
        # Retrieve configuration
        config = self.env['ir.config_parameter'].sudo()
        api_url = config.get_param('logistics_3pl_connector.api_url')
        api_key = config.get_param('logistics_3pl_connector.api_key')
        target_warehouse_id = config.get_param('logistics_3pl_connector.warehouse_id')
        allow_resend = config.get_param('logistics_3pl_connector.allow_resend', 'False').lower() == 'true'
        
        # Determine send type for logging and messages
        previous_status = self.x_3pl_status
        is_resend = previous_status in ('sent', 'shipped', 'delivered')
        is_retry = previous_status == 'error'
        
        # Check if resend is allowed
        if is_resend and not allow_resend:
            raise UserError(_(
                "Resend to 3PL is not enabled. "
                "Please enable 'Allow Resend to 3PL' in Inventory Settings to use this feature."
            ))
        
        # Log appropriately based on status
        if is_resend:
            _logger.info(f"Resending picking {self.name} to e-Transport (previous status: {previous_status})")
        elif is_retry:
            _logger.info(f"Retrying send to e-Transport for picking {self.name} (previous attempt failed)")
        else:
            _logger.info(f"Sending picking {self.name} to e-Transport for the first time")

        # Check warehouse filter
        if target_warehouse_id:
            try:
                target_wh_id = int(target_warehouse_id)
                if self.picking_type_id.warehouse_id.id != target_wh_id:
                    raise UserError(_("This transfer does not belong to the configured 3PL Warehouse."))
            except (ValueError, TypeError):
                _logger.warning("Invalid 3PL Warehouse ID in configuration.")

        if not api_url or not api_key:
            raise UserError(_("3PL API configuration is missing. Please check Inventory Settings."))

        # Build e-Transport payload
        payload = self._prepare_etransport_payload()
        
        headers = {
            'Content-Type': 'application/json',
            'X-API-Key': api_key  # e-Transport uses X-API-Key header
        }

        try:
            import json as json_lib
            _logger.info(f"Sending Picking {self.name} to e-Transport at {api_url}/tms/import-data")
            _logger.debug(f"Payload being sent: {json_lib.dumps(payload, indent=2, default=str)}")
            
            response = requests.post(
                f"{api_url}/tms/import-data",
                json=payload,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                response_data = response.json()
                status = response_data.get('status', '')
                
                # Log full response for debugging
                _logger.info(f"e-Transport response for {self.name}: {json_lib.dumps(response_data, indent=2, default=str)}")
                
                if status in ('success', 'warning'):
                    # Get TMS ID from mapping if available
                    # e-Transport returns: {"mapping": {"orders": {"WH/OUT/00001": 4589}}}
                    mapping = response_data.get('mapping', {})
                    orders_mapping = mapping.get('orders', {})
                    tms_id = orders_mapping.get(self.name)
                    
                    _logger.info(f"e-Transport mapping for {self.name}: mapping={mapping}, tms_id={tms_id}")
                    
                    # Update 3PL fields - use TMS ID if available, otherwise use our reference
                    vals = {
                        'x_3pl_order_id': str(tms_id) if tms_id else self.name,
                        'x_3pl_status': 'sent'
                    }
                    self.write(vals)
                    
                    # Build message with details
                    msg_parts = []
                    if is_resend:
                        msg_parts.append(_("🔄 Resent to e-Transport (previous status: %s)") % previous_status)
                    elif is_retry:
                        msg_parts.append(_("🔄 Re-sent to e-Transport."))
                    else:
                        msg_parts.append(_("📤 Sent to e-Transport."))
                    
                    if tms_id:
                        msg_parts.append(_("TMS ID: %s") % tms_id)
                    
                    orders_created = response_data.get('orders_created', 0)
                    orders_updated = response_data.get('orders_updated', 0)
                    if orders_created:
                        msg_parts.append(_("Orders created: %s") % orders_created)
                    if orders_updated:
                        msg_parts.append(_("Orders updated: %s") % orders_updated)
                    
                    # Add warnings if any
                    warnings = response_data.get('warnings', [])
                    if warnings:
                        msg_parts.append(_("⚠️ Warnings: %s") % ', '.join(warnings))
                    
                    # Build message body
                    msg_body = ' | '.join(msg_parts)
                    
                    self.message_post(body=msg_body)
                    
                else:
                    # Error status
                    errors = response_data.get('errors', [])
                    warnings = response_data.get('warnings', [])
                    error_msg = _("❌ e-Transport Error: %s") % status
                    if errors:
                        error_msg += " | " + _("Errors: %s") % ', '.join(errors)
                    if warnings:
                        error_msg += " | " + _("Warnings: %s") % ', '.join(warnings)
                    
                    self.write({'x_3pl_status': 'error'})
                    self.message_post(body=error_msg)
                    _logger.error(f"e-Transport Error for {self.name}: {json_lib.dumps(response_data, indent=2, default=str)}")
                    raise UserError(_("e-Transport Error: %s") % status)
            else:
                error_msg = _("❌ e-Transport API Error: HTTP %s") % response.status_code
                self.write({'x_3pl_status': 'error'})
                self.message_post(body=error_msg)
                _logger.error(f"e-Transport API Error for {self.name}: {response.status_code} - {response.text}")
                raise UserError(_("e-Transport API Error: HTTP %s") % response.status_code)
                
        except requests.exceptions.RequestException as e:
            error_msg = f"Connection Error: {str(e)}"
            self.write({'x_3pl_status': 'error'})
            self.message_post(body=error_msg)
            _logger.error(error_msg)
            raise UserError(error_msg)
    
    def action_fetch_tracking(self):
        """
        Manually fetch tracking status from e-Transport TMS.
        Uses GET /tms/tracking/{external_ref} endpoint.
        """
        self.ensure_one()
        
        if self.x_3pl_status not in ('sent', 'shipped'):
            raise UserError(_("Tracking is only available for orders that have been sent to e-Transport."))
        
        config = self.env['ir.config_parameter'].sudo()
        api_url = config.get_param('logistics_3pl_connector.api_url')
        api_key = config.get_param('logistics_3pl_connector.api_key')
        tracking_url_base = config.get_param('logistics_3pl_connector.tracking_url_base', '')
        
        if not api_url or not api_key:
            raise UserError(_("3PL API configuration is missing. Please check Inventory Settings."))
        
        headers = {
            'X-API-Key': api_key
        }
        
        # Use the picking name as external_ref (same as what we sent)
        external_ref = self.name
        
        try:
            _logger.info(f"Fetching tracking for {external_ref} from e-Transport")
            
            response = requests.get(
                f"{api_url}/tms/tracking/{external_ref}",
                headers=headers,
                params={
                    'include_traceability': 'true',
                    'include_packs': 'true',
                    'traceability_limit': 10
                },
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                current_state = data.get('current_state', '')
                eta = data.get('eta')
                time_range = data.get('time_range')
                traceability = data.get('traceability', [])
                
                # Map e-Transport states to our internal status
                vals = {
                    'x_3pl_current_state': current_state,
                }
                
                # Update status based on e-Transport state
                state_lower = current_state.lower() if current_state else ''
                if state_lower in ('delivered', 'completed', 'done', 'entregado'):
                    vals['x_3pl_status'] = 'delivered'
                elif state_lower in ('in_transit', 'on_route', 'en_ruta', 'shipped', 'enviado'):
                    vals['x_3pl_status'] = 'shipped'
                
                # Build tracking URL if we have a reference
                if tracking_url_base and external_ref:
                    if not tracking_url_base.endswith('/') and not tracking_url_base.endswith('='):
                        tracking_url_base += '/'
                    vals['x_3pl_tracking_url'] = f"{tracking_url_base}{external_ref}"
                
                self.write(vals)
                
                # Build message with tracking info
                msg_parts = [_("📍 Tracking updated from e-Transport")]
                msg_parts.append(_("State: %s") % (current_state or 'Unknown'))
                
                if eta:
                    msg_parts.append(_("ETA: %s") % eta)
                if time_range:
                    msg_parts.append(_("Time Range: %s") % time_range)
                
                # Add recent traceability events
                if traceability:
                    msg_parts.append(_("\nRecent events:"))
                    for event in traceability[:5]:  # Show last 5 events
                        timestamp = event.get('timestamp', '')
                        event_name = event.get('event', event.get('state', ''))
                        location = event.get('location', '')
                        event_line = f"• {timestamp}: {event_name}"
                        if location:
                            event_line += f" ({location})"
                        msg_parts.append(event_line)
                
                self.message_post(body='<br/>'.join(msg_parts))
                
                # If delivered, offer to validate the picking
                if vals.get('x_3pl_status') == 'delivered' and self.state == 'waiting_3pl':
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _("Delivery Confirmed"),
                            'message': _("e-Transport reports this delivery as completed. You can now validate the picking."),
                            'type': 'success',
                            'sticky': False,
                        }
                    }
                    
            elif response.status_code == 404:
                self.message_post(body=_(
                    "⚠️ Order %s not found in e-Transport. It may not have been processed yet."
                ) % external_ref)
            else:
                error_msg = f"e-Transport Tracking Error: {response.status_code} - {response.text}"
                self.message_post(body=error_msg)
                _logger.warning(error_msg)
                
        except requests.exceptions.RequestException as e:
            error_msg = f"Connection Error fetching tracking: {str(e)}"
            self.message_post(body=error_msg)
            _logger.error(error_msg)
            raise UserError(error_msg)
    
    def button_validate(self):
        """Override to handle auto-send to 3PL and block validation when waiting for 3PL confirmation."""
        # If skip_3pl_check is True (from webhook or force validate), skip all 3PL logic
        if self.env.context.get('skip_3pl_check'):
            return super().button_validate()
        
        # Skip auto-send logic if already processed (to avoid recursion)
        if self.env.context.get('skip_3pl_auto_send'):
            return super().button_validate()
        
        # Check if auto-send is enabled
        config = self.env['ir.config_parameter'].sudo()
        auto_send = config.get_param('logistics_3pl_connector.auto_send', 'False').lower() == 'true'
        web_only = config.get_param('logistics_3pl_connector.web_orders_only', 'False').lower() == 'true'
        
        pickings_sent_to_3pl = self.browse()  # Track pickings successfully sent to 3PL
        
        for picking in self:
            # Block validation if waiting for 3PL
            if picking.state == 'waiting_3pl':
                raise UserError(_("This delivery is waiting for 3PL confirmation. Use 'Force Validate' to override."))
            
            # Auto-send conditions
            is_eligible_for_auto_send = (
                auto_send 
                and picking.state == 'assigned' 
                and picking.x_3pl_eligible 
                and picking.x_3pl_status in ('draft', 'error')
            )
            
            # Apply web-only filter if enabled
            if is_eligible_for_auto_send and web_only and not picking.x_is_web_order:
                _logger.debug(f"Skipping auto-send for {picking.name}: web_orders_only is enabled and this is not a web order")
                is_eligible_for_auto_send = False
            
            # Auto-send to 3PL if eligible
            if is_eligible_for_auto_send:
                try:
                    picking.action_send_to_3pl()
                    # If successful, picking state changed to 'waiting_3pl', don't validate
                    pickings_sent_to_3pl |= picking
                except Exception as e:
                    _logger.warning(f"Auto-send to 3PL failed for {picking.name}: {e}")
                    # Continue with validation even if auto-send fails
        
        # Only validate pickings that were NOT sent to 3PL (or failed to send)
        # Filter out pickings that are now in 'waiting_3pl' state (successfully sent to 3PL)
        pickings_to_validate = self.filtered(lambda p: p.id not in pickings_sent_to_3pl.ids or p.state != 'waiting_3pl')
        
        if pickings_sent_to_3pl:
            # Some pickings were sent to 3PL and are now in 'waiting_3pl' state
            # They should wait for webhook confirmation before validation
            if pickings_to_validate:
                # Some pickings can still be validated (not eligible or auto-send disabled)
                # Call parent method only on pickings that weren't sent to 3PL, skip auto-send logic
                return pickings_to_validate.with_context(skip_3pl_auto_send=True).button_validate()
            else:
                # All pickings were sent to 3PL and are now in 'waiting_3pl' state, no validation should occur
                return True
        
        # No pickings were sent to 3PL, proceed with normal validation
        return super().button_validate()
    
    def action_force_validate(self):
        """
        Force validate a picking that is waiting for 3PL (manual override).
        Uses the same context flags as webhook auto-validation to skip wizards.
        """
        self.ensure_one()
        if self.state != 'waiting_3pl':
            raise UserError(_("This action is only available for pickings waiting for 3PL."))
        
        # Use the same context flags as webhook auto-validation to:
        # - skip_3pl_check: bypass our 3PL blocking logic
        # - skip_3pl_auto_send: prevent recursion
        # - skip_sms: skip SMS confirmation wizard (if SMS module is installed)
        # - skip_backorder: auto-handle backorders without wizard
        # - button_validate_picking_ids: required for batch validation
        validate_ctx = {
            'skip_3pl_check': True,
            'skip_3pl_auto_send': True,
            'skip_sms': True,
            'skip_backorder': True,
            'button_validate_picking_ids': self.ids,
        }
        return self.with_context(**validate_ctx).button_validate()
