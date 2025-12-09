# -*- coding: utf-8 -*-
{
    'name': "3PL Logistics Connector",
    'summary': """
        Integrate Odoo with Generic 3PL via REST API""",
    'description': """
        This module enables:
        - Outbound: Sending Delivery Orders to 3PL upon validation.
        - Inbound: Receiving status updates and tracking numbers via Webhooks.
    """,
    'author': "GatBrewing",
    'website': "https://gatbrewing.beer",
    'category': 'Inventory/Inventory',
    'version': '19.0.1.0.0',
    'depends': ['stock'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/stock_picking_views.xml',
    ],
    'license': 'LGPL-3',
}

