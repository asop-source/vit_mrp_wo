from datetime import datetime
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_round
from odoo.addons import decimal_precision as dp




class vit_mrp_production(models.Model):
	_inherit = 'mrp.production'


	def _workorders_create(self, bom, bom_data):
		"""
		:param bom: in case of recursive boms: we could create work orders for child
					BoMs
		"""
		workorders = self.env['mrp.workorder']
		bom_qty = bom_data['qty']

		# Initial qty producing
		if self.product_id.tracking == 'serial':
			quantity = 1.0
		else:
			quantity = self.product_qty - sum(self.move_finished_ids.mapped('quantity_done'))
			quantity = quantity if (quantity > 0) else 0

		for operation in bom.routing_id.operation_ids:
			# create workorder
			cycle_number = float_round(bom_qty / operation.workcenter_id.capacity, precision_digits=0, rounding_method='UP')
			duration_expected = (operation.workcenter_id.time_start +
								 operation.workcenter_id.time_stop +
								 cycle_number * operation.time_cycle * 100.0 / operation.workcenter_id.time_efficiency)
			workorder = workorders.create({
				'name': operation.name,
				'production_id': self.id,
				'workcenter_id': operation.workcenter_id.id,
				'operation_id': operation.id,
				'duration_expected': duration_expected,
				'state': len(workorders) == 0 and 'ready' or 'pending',
				'qty_producing': quantity,
				'qty_product': quantity,
				'capacity': operation.workcenter_id.capacity,
			})
			if workorders:
				workorders[-1].next_work_order_id = workorder.id
				workorders[-1]._start_nextworkorder()
			workorders += workorder

			# assign moves; last operation receive all unassigned moves (which case ?)
			moves_raw = self.move_raw_ids.filtered(lambda move: move.operation_id == operation)
			if len(workorders) == len(bom.routing_id.operation_ids):
				moves_raw |= self.move_raw_ids.filtered(lambda move: not move.operation_id)
			moves_finished = self.move_finished_ids.filtered(lambda move: move.operation_id == operation) #TODO: code does nothing, unless maybe by_products?
			moves_raw.mapped('move_line_ids').write({'workorder_id': workorder.id})
			(moves_finished + moves_raw).write({'workorder_id': workorder.id})

			workorder._generate_lot_ids()
		return workorders






class vit_mrp_wo(models.Model):
	_inherit = 'mrp.workorder'

	qty_product = fields.Float('Original Production Quantity', readonly=True,)




	@api.multi
	def record_production(self):
		if not self:
			return True

		self.ensure_one()
		# if self.qty_producing <= 0:
		# 	raise UserError(_('Please set the quantity you are currently producing. It should be different from zero.'))

		if (self.production_id.product_id.tracking != 'none') and not self.final_lot_id and self.move_raw_ids:
			raise UserError(_('You should provide a lot/serial number for the final product.'))

		# Update quantities done on each raw material line
		# For each untracked component without any 'temporary' move lines,
		# (the new workorder tablet view allows registering consumed quantities for untracked components)
		# we assume that only the theoretical quantity was used
		for move in self.move_raw_ids:
			if move.has_tracking == 'none' and (move.state not in ('done', 'cancel')) and move.bom_line_id\
						and move.unit_factor and not move.move_line_ids.filtered(lambda ml: not ml.done_wo):
				rounding = move.product_uom.rounding
				if self.product_id.tracking != 'none':
					qty_to_add = float_round(self.qty_producing * move.unit_factor, precision_rounding=rounding)
					move._generate_consumed_move_line(qty_to_add, self.final_lot_id)
				elif len(move._get_move_lines()) < 2:
					move.quantity_done += float_round(self.qty_producing * move.unit_factor, precision_rounding=rounding)
				else:
					move._set_quantity_done(move.quantity_done + float_round(self.qty_producing * move.unit_factor, precision_rounding=rounding))

		# Transfer quantities from temporary to final move lots or make them final
		for move_line in self.active_move_line_ids:
			# Check if move_line already exists
			if move_line.qty_done <= 0:  # rounding...
				move_line.sudo().unlink()
				continue
			if move_line.product_id.tracking != 'none' and not move_line.lot_id:
				raise UserError(_('You should provide a lot/serial number for a component.'))
			# Search other move_line where it could be added:
			lots = self.move_line_ids.filtered(lambda x: (x.lot_id.id == move_line.lot_id.id) and (not x.lot_produced_id) and (not x.done_move) and (x.product_id == move_line.product_id))
			if lots:
				lots[0].qty_done += move_line.qty_done
				lots[0].lot_produced_id = self.final_lot_id.id
				self._link_to_quality_check(move_line, lots[0])
				move_line.sudo().unlink()
			else:
				move_line.lot_produced_id = self.final_lot_id.id
				move_line.done_wo = True

		self.move_line_ids.filtered(
			lambda move_line: not move_line.done_move and not move_line.lot_produced_id and move_line.qty_done > 0
		).write({
			'lot_produced_id': self.final_lot_id.id,
			'lot_produced_qty': self.qty_producing
		})

		# If last work order, then post lots used
		# TODO: should be same as checking if for every workorder something has been done?
		if not self.next_work_order_id:
			production_move = self.production_id.move_finished_ids.filtered(
								lambda x: (x.product_id.id == self.production_id.product_id.id) and (x.state not in ('done', 'cancel')))
			if production_move.product_id.tracking != 'none':
				move_line = production_move.move_line_ids.filtered(lambda x: x.lot_id.id == self.final_lot_id.id)
				if move_line:
					move_line.product_uom_qty += self.qty_producing
					move_line.qty_done += self.qty_producing
				else:
					location_dest_id = production_move.location_dest_id.get_putaway_strategy(self.product_id).id or production_move.location_dest_id.id
					move_line.create({'move_id': production_move.id,
							 'product_id': production_move.product_id.id,
							 'lot_id': self.final_lot_id.id,
							 'product_uom_qty': self.qty_producing,
							 'product_uom_id': production_move.product_uom.id,
							 'qty_done': self.qty_producing,
							 'workorder_id': self.id,
							 'location_id': production_move.location_id.id,
							 'location_dest_id': location_dest_id,
					})
			else:
				production_move._set_quantity_done(self.qty_producing)

		if not self.next_work_order_id:
			for by_product_move in self._get_byproduct_move_to_update():
					if by_product_move.has_tracking != 'serial':
						values = self._get_byproduct_move_line(by_product_move, self.qty_producing * by_product_move.unit_factor)
						self.env['stock.move.line'].create(values)
					elif by_product_move.has_tracking == 'serial':
						qty_todo = by_product_move.product_uom._compute_quantity(self.qty_producing * by_product_move.unit_factor, by_product_move.product_id.uom_id)
						for i in range(0, int(float_round(qty_todo, precision_digits=0))):
							values = self._get_byproduct_move_line(by_product_move, 1)
							self.env['stock.move.line'].create(values)

		# Update workorder quantity produced
		self.qty_produced += self.qty_producing

		if self.final_lot_id:
			self.final_lot_id.use_next_on_work_order_id = self.next_work_order_id
			self.final_lot_id = False

		# One a piece is produced, you can launch the next work order
		self._start_nextworkorder()


		# Set a qty producing
		rounding = self.production_id.product_uom_id.rounding
		obj_work = self.env['mrp.workorder'].search([('production_id.name', '=', self.production_id.name),('state', '!=','done')])
		obj_mrp = self.env['mrp.production'].search([('name','=',self.production_id.name)])
		obj_picking = self.env['stock.picking'].search([('origin','=',self.production_id.name)])
		if float_compare(self.qty_produced, self.qty_product, precision_rounding=rounding) >= 0:
			self.qty_producing = 0
		elif self.production_id.product_id.tracking == 'serial':
			self._assign_default_final_lot_id()
			self.qty_producing = 1.0
			self._generate_lot_ids()
		else:
			if self.qty_producing != 0:
				self.qty_producing = float_round(self.qty_product - self.qty_produced, precision_rounding=rounding)
			self._generate_lot_ids()
		

		if self.next_work_order_id and self.next_work_order_id.state not in ['done', 'cancel'] and self.production_id.product_id.tracking != 'none':
			self.next_work_order_id._assign_default_final_lot_id()

		if self.qty_producing == 0 or float_compare(self.qty_produced, self.qty_product, precision_rounding=rounding) >= 0:
			obj_work.write({'qty_product': self.qty_produced})
			obj_work.write({'qty_producing': self.qty_produced})
			self.button_finish()

		for x in obj_mrp:
			x.write({'product_qty': self.qty_product})
		
		for x in obj_picking:
			x.move_ids_without_package.write({'product_uom_qty': self.qty_product})

		return True



	def _get_byproduct_move_to_update(self):
		return self.production_id.move_finished_ids.filtered(lambda x: (x.product_id.id != self.production_id.product_id.id) and (x.state not in ('done', 'cancel')))

	@api.multi
	def _start_nextworkorder(self):
		rounding = self.product_id.uom_id.rounding
		if self.next_work_order_id.state == 'pending' and (
				(self.operation_id.batch == 'no' and
				 float_compare(self.qty_product, self.qty_produced, precision_rounding=rounding) <= 0) or
				(self.operation_id.batch == 'yes' and
				 float_compare(self.operation_id.batch_size, self.qty_produced, precision_rounding=rounding) <= 0)):
			self.next_work_order_id.state = 'ready'


	@api.multi
	def button_finish(self):
		self.ensure_one()
		self.end_all()
		return self.write({'qty_producing' : 0,'state': 'done', 'date_finished': fields.Datetime.now()})


	@api.multi
	def end_all(self):
		return self.end_previous(doall=True)


	@api.multi
	@api.depends('qty_product', 'qty_produced')
	def _compute_qty_remaining(self):
		for wo in self:
			wo.qty_remaining = float_round(wo.qty_product - wo.qty_produced, precision_rounding=wo.production_id.product_uom_id.rounding)



