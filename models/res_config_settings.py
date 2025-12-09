from odoo import fields, models, api

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    logistics_3pl_api_url = fields.Char(
        string="3PL API URL",
        config_parameter='logistics_3pl_connector.api_url',
        help="Base URL for the 3PL API (e.g., https://api.3plprovider.com)"
    )
    logistics_3pl_api_key = fields.Char(
        string="3PL API Key",
        config_parameter='logistics_3pl_connector.api_key',
        help="Authentication key for the 3PL API"
    )
    logistics_3pl_auto_send = fields.Boolean(
        string="Auto Send to 3PL",
        config_parameter='logistics_3pl_connector.auto_send',
        help="Automatically send Delivery Orders to 3PL upon validation"
    )
    logistics_3pl_tracking_url_base = fields.Char(
        string="Tracking URL Base",
        config_parameter='logistics_3pl_connector.tracking_url_base',
        default='https://tracking.example.com/odoo/',
        help="Base URL for tracking. The tracking number will be appended. E.g., https://tracking.example.com/odoo/"
    )
    # Explicitly remove config_parameter from here as it causes issues with Many2one
    logistics_3pl_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="3PL Warehouse",
        help="If set, only Delivery Orders from this warehouse will be sent to the 3PL."
    )

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        config_param = self.env['ir.config_parameter'].sudo()
        warehouse_id = config_param.get_param('logistics_3pl_connector.warehouse_id')
        if warehouse_id:
            try:
                res.update(logistics_3pl_warehouse_id=int(warehouse_id))
            except (ValueError, TypeError):
                res.update(logistics_3pl_warehouse_id=False)
        else:
             res.update(logistics_3pl_warehouse_id=False)
        return res

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        config_param = self.env['ir.config_parameter'].sudo()
        # Store as integer ID or False
        value = self.logistics_3pl_warehouse_id.id if self.logistics_3pl_warehouse_id else False
        config_param.set_param('logistics_3pl_connector.warehouse_id', value)
