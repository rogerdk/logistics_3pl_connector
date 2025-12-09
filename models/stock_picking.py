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
        ('error', 'Error'),
    ], string="3PL Status", default='draft', copy=False, tracking=True)
    x_3pl_tracking_ref = fields.Char(string="Tracking Number", readonly=True, copy=False)
    x_3pl_tracking_url = fields.Char(string="Tracking URL", readonly=True, copy=False)
    x_3pl_eligible = fields.Boolean(compute='_compute_3pl_eligible', store=False)
    
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

    def action_send_to_3pl(self):
        """Send picking to 3PL. Changes state to 'waiting_3pl' on success."""
        self.ensure_one()
        
        # Retrieve configuration
        config = self.env['ir.config_parameter'].sudo()
        api_url = config.get_param('logistics_3pl_connector.api_url')
        api_key = config.get_param('logistics_3pl_connector.api_key')
        target_warehouse_id = config.get_param('logistics_3pl_connector.warehouse_id')

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

        # Construct payload
        payload = {
            'order_reference': self.name,
            'partner_name': self.partner_id.name,
            'partner_address': self.partner_id.contact_address,
            'lines': []
        }

        for move in self.move_ids:
            payload['lines'].append({
                'product_code': move.product_id.default_code,
                'product_name': move.product_id.name,
                'quantity': move.product_uom_qty,
            })

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }

        try:
            _logger.info(f"Sending Picking {self.name} to 3PL at {api_url}")
            response = requests.post(f"{api_url}/orders", json=payload, headers=headers, timeout=10)
            
            if response.status_code in (200, 201):
                response_data = response.json()
                external_id = response_data.get('order_id')
                
                vals = {
                    'x_3pl_order_id': external_id,
                    'x_3pl_status': 'sent'
                }
                # Only change to waiting_3pl if not already done
                if self.state == 'assigned':
                    vals['state'] = 'waiting_3pl'
                    self.write(vals)
                    self.message_post(body=_("📤 Sent to 3PL. External ID: %s. Waiting for confirmation...") % external_id)
                else:
                    # Already done, just update 3PL status
                    self.write(vals)
                    self.message_post(body=_("📤 Sent to 3PL. External ID: %s") % external_id)
            else:
                error_msg = f"3PL API Error: {response.status_code} - {response.text}"
                self.write({'x_3pl_status': 'error'})
                self.message_post(body=error_msg)
                _logger.error(error_msg)
                raise UserError(error_msg)
                
        except requests.exceptions.RequestException as e:
            error_msg = f"Connection Error: {str(e)}"
            self.write({'x_3pl_status': 'error'})
            self.message_post(body=error_msg)
            _logger.error(error_msg)
            raise UserError(error_msg)
    
    def button_validate(self):
        """Override to handle auto-send to 3PL and block validation when waiting for 3PL confirmation."""
        # Skip auto-send logic if already processed (to avoid recursion)
        if self.env.context.get('skip_3pl_auto_send'):
            return super().button_validate()
        
        # Check if auto-send is enabled
        config = self.env['ir.config_parameter'].sudo()
        auto_send = config.get_param('logistics_3pl_connector.auto_send', 'False').lower() == 'true'
        
        pickings_sent_to_3pl = self.browse()  # Track pickings successfully sent to 3PL
        
        for picking in self:
            # Block validation if waiting for 3PL
            if picking.state == 'waiting_3pl' and not self.env.context.get('skip_3pl_check'):
                raise UserError(_("This delivery is waiting for 3PL confirmation. Use 'Force Validate' to override."))
            
            # Auto-send to 3PL if enabled and picking is eligible
            if auto_send and picking.state == 'assigned' and picking.x_3pl_eligible and picking.x_3pl_status in ('draft', 'error'):
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
        """Force validate a picking that is waiting for 3PL (manual override)."""
        self.ensure_one()
        if self.state != 'waiting_3pl':
            raise UserError(_("This action is only available for pickings waiting for 3PL."))
        return self.with_context(skip_3pl_check=True).button_validate()
