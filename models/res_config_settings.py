from odoo import fields, models, api

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # === Connection Settings ===
    logistics_3pl_api_url = fields.Char(
        string="3PL API URL",
        config_parameter='logistics_3pl_connector.api_url',
        help="Base URL for e-Transport API (e.g., https://e-transport.es/api)"
    )
    logistics_3pl_api_key = fields.Char(
        string="3PL API Key",
        config_parameter='logistics_3pl_connector.api_key',
        help="X-API-Key token provided by e-Transport"
    )
    
    # === e-Transport Specific Settings ===
    logistics_3pl_shipment_type = fields.Char(
        string="ShipmentType (eCommerce)",
        config_parameter='logistics_3pl_connector.shipment_type',
        default='E',
        help="ShipmentType code for eCommerce orders (default: 'E' for Entrega)"
    )
    logistics_3pl_shipment_type_internal = fields.Char(
        string="ShipmentType (Internal)",
        config_parameter='logistics_3pl_connector.shipment_type_internal',
        default='M',
        help="ShipmentType code for internal/manual orders (default: 'M' for Movimiento)"
    )
    logistics_3pl_service_type = fields.Char(
        string="ServiceType",
        config_parameter='logistics_3pl_connector.service_type',
        default='ND_3H',
        help="ServiceType code (e.g., 'ND_3H' for 3-hour delivery window)"
    )
    logistics_3pl_default_temperature = fields.Selection([
        ('AM', 'Ambiente'),
        ('FR', 'Frío'),
        ('CO', 'Congelado'),
    ], string="Default Temperature",
        config_parameter='logistics_3pl_connector.default_temperature',
        default='FR',
        help="Default temperature for goods (AM=Ambiente, FR=Frío, CO=Congelado)"
    )
    
    # === Automation Settings ===
    logistics_3pl_auto_send = fields.Boolean(
        string="Auto Send to 3PL",
        config_parameter='logistics_3pl_connector.auto_send',
        help="Automatically send Delivery Orders to 3PL upon validation"
    )
    logistics_3pl_web_orders_only = fields.Boolean(
        string="Web Orders Only",
        config_parameter='logistics_3pl_connector.web_orders_only',
        default=False,
        help="If enabled, auto-send will only apply to orders placed through the eCommerce website. "
             "Manual/backend orders will not be sent automatically (but can still be sent manually)."
    )
    logistics_3pl_allow_resend = fields.Boolean(
        string="Allow Resend to 3PL",
        config_parameter='logistics_3pl_connector.allow_resend',
        default=False,
        help="If enabled, allows resending orders to e-Transport even if they were already sent. "
             "Useful for testing or when an order needs to be re-transmitted. "
             "Use with caution in production as it may create duplicate orders in the 3PL system."
    )
    
    # === Tracking Settings ===
    logistics_3pl_tracking_url_base = fields.Char(
        string="Tracking URL Base",
        config_parameter='logistics_3pl_connector.tracking_url_base',
        default='https://e-transport.es/tracking/',
        help="Base URL for tracking. The tracking number will be appended."
    )
    
    # === Warehouse & User Settings ===
    # Explicitly remove config_parameter from here as it causes issues with Many2one
    logistics_3pl_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="3PL Warehouse",
        help="If set, only Delivery Orders from this warehouse will be sent to the 3PL."
    )
    logistics_3pl_webhook_user_id = fields.Many2one(
        'res.users',
        string="Webhook User",
        help="User for automated webhook operations (auto-validation). "
             "For security, create a dedicated user with only Inventory permissions. "
             "If not set, OdooBot (superuser) will be used as fallback."
    )

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        config_param = self.env['ir.config_parameter'].sudo()
        
        # Get warehouse_id
        warehouse_id = config_param.get_param('logistics_3pl_connector.warehouse_id')
        if warehouse_id:
            try:
                res.update(logistics_3pl_warehouse_id=int(warehouse_id))
            except (ValueError, TypeError):
                res.update(logistics_3pl_warehouse_id=False)
        else:
            res.update(logistics_3pl_warehouse_id=False)
        
        # Get webhook_user_id
        webhook_user_id = config_param.get_param('logistics_3pl_connector.webhook_user_id')
        if webhook_user_id:
            try:
                res.update(logistics_3pl_webhook_user_id=int(webhook_user_id))
            except (ValueError, TypeError):
                res.update(logistics_3pl_webhook_user_id=False)
        else:
            res.update(logistics_3pl_webhook_user_id=False)
        
        return res

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        config_param = self.env['ir.config_parameter'].sudo()
        
        # Store warehouse_id as integer ID or False
        warehouse_value = self.logistics_3pl_warehouse_id.id if self.logistics_3pl_warehouse_id else False
        config_param.set_param('logistics_3pl_connector.warehouse_id', warehouse_value)
        
        # Store webhook_user_id as integer ID or False
        user_value = self.logistics_3pl_webhook_user_id.id if self.logistics_3pl_webhook_user_id else False
        config_param.set_param('logistics_3pl_connector.webhook_user_id', user_value)
