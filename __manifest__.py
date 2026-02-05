# -*- coding: utf-8 -*-
{
    'name': "e-Transport 3PL Connector",
    'summary': """
        Integrate Odoo with e-Transport TMS via REST API""",
    'description': """
        This module enables integration with e-Transport TMS (https://e-transport.es):
        
        Features:
        - Outbound: Send Delivery Orders to e-Transport TMS upon validation
        - Tracking: Manual fetch of tracking status from e-Transport
        - Webhooks: Receive status updates via webhooks (optional)
        - Temperature support: AM (Ambiente), FR (Frío), CO (Congelado)
        - Delivery time slots integration
        
        API Endpoints used:
        - POST /tms/import-data (create/update orders)
        - GET /tms/tracking/{external_ref} (fetch tracking status)
    """,
    'author': "GatBrewing",
    'website': "https://gatbrewing.beer",
    'category': 'Inventory/Inventory',
    'version': '19.0.2.0.0',
    'depends': ['stock', 'sale_stock'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/stock_picking_views.xml',
    ],
    'license': 'LGPL-3',
}

