# -*- coding: utf-8 -*-
from odoo import http

# class VitMrpWo(http.Controller):
#     @http.route('/vit_mrp_wo/vit_mrp_wo/', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/vit_mrp_wo/vit_mrp_wo/objects/', auth='public')
#     def list(self, **kw):
#         return http.request.render('vit_mrp_wo.listing', {
#             'root': '/vit_mrp_wo/vit_mrp_wo',
#             'objects': http.request.env['vit_mrp_wo.vit_mrp_wo'].search([]),
#         })

#     @http.route('/vit_mrp_wo/vit_mrp_wo/objects/<model("vit_mrp_wo.vit_mrp_wo"):obj>/', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('vit_mrp_wo.object', {
#             'object': obj
#         })