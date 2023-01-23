# Copyright 2011-2020 Akretion France (http://www.akretion.com)
# Copyright 2009-2022 Noviat (http://www.noviat.com)
# @author Alexis de Lattre <alexis.delattre@akretion.com>
# @author Luc de Meyer <info@noviat.com>

import logging
from datetime import date

from dateutil.relativedelta import relativedelta
from stdnum.vatin import is_valid

from odoo import _, api, fields, models
from odoo.exceptions import RedirectWarning, UserError, ValidationError
from odoo.tools import float_is_zero

_logger = logging.getLogger(__name__)


class IntrastatProductDeclaration(models.Model):
    _name = "intrastat.product.declaration"
    _description = "Intrastat Product Report Base Object"
    _rec_name = "year_month"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "year_month desc, declaration_type, revision"
    _sql_constraints = [
        (
            "date_uniq",
            "unique(year_month, company_id, declaration_type, revision)",
            "A declaration of the same type already exists for this month !"
            "\nYou should update the existing declaration "
            "or change the revision number of this one.",
        )
    ]

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        decl_date = fields.Date.context_today(self) - relativedelta(months=1)
        res.update(
            {"year": str(decl_date.year), "month": str(decl_date.month).zfill(2)}
        )
        return res

    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        states={"done": [("readonly", True)]},
        default=lambda self: self.env.company,
    )
    company_country_code = fields.Char(
        related="company_id.country_id.code",
        string="Company Country Code",
        store=True,
    )
    state = fields.Selection(
        selection=[("draft", "Draft"), ("done", "Done")],
        string="State",
        readonly=True,
        tracking=True,
        copy=False,
        default="draft",
        help="State of the declaration. When the state is set to 'Done', "
        "the parameters become read-only.",
    )
    note = fields.Text(
        string="Notes", help="You can add some comments here if you want."
    )
    year = fields.Char(
        string="Year", required=True, states={"done": [("readonly", True)]}
    )
    month = fields.Selection(
        selection=[
            ("01", "01"),
            ("02", "02"),
            ("03", "03"),
            ("04", "04"),
            ("05", "05"),
            ("06", "06"),
            ("07", "07"),
            ("08", "08"),
            ("09", "09"),
            ("10", "10"),
            ("11", "11"),
            ("12", "12"),
        ],
        string="Month",
        required=True,
        states={"done": [("readonly", True)]},
    )
    year_month = fields.Char(
        compute="_compute_year_month",
        string="Period",
        readonly=True,
        tracking=True,
        store=True,
        help="Year and month of the declaration.",
    )
    declaration_type = fields.Selection(
        selection="_get_declaration_type",
        string="Type",
        required=True,
        states={"done": [("readonly", True)]},
        tracking=True,
    )
    action = fields.Selection(
        selection="_get_action",
        string="Action",
        required=True,
        default="replace",
        states={"done": [("readonly", True)]},
        tracking=True,
    )
    revision = fields.Integer(
        string="Revision",
        default=1,
        states={"done": [("readonly", True)]},
        help="Used to keep track of changes",
    )
    computation_line_ids = fields.One2many(
        comodel_name="intrastat.product.computation.line",
        inverse_name="parent_id",
        string="Intrastat Product Computation Lines",
        states={"done": [("readonly", True)]},
    )
    declaration_line_ids = fields.One2many(
        comodel_name="intrastat.product.declaration.line",
        inverse_name="parent_id",
        string="Intrastat Product Declaration Lines",
        readonly=True,
    )
    num_decl_lines = fields.Integer(
        compute="_compute_numbers",
        string="Number of Declaration Lines",
        store=True,
        tracking=True,
    )
    total_amount = fields.Integer(
        compute="_compute_numbers",
        string="Total Fiscal Amount",
        store=True,
        help="Total fiscal amount in company currency of the declaration.",
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", string="Currency"
    )
    reporting_level = fields.Selection(
        selection="_get_reporting_level",
        compute="_compute_reporting_level",
        readonly=False,
        string="Reporting Level",
        states={"done": [("readonly", True)]},
    )
    xml_attachment_id = fields.Many2one("ir.attachment", string="XML Attachment")
    xml_attachment_datas = fields.Binary(
        related="xml_attachment_id.datas", string="XML Export"
    )
    xml_attachment_name = fields.Char(
        related="xml_attachment_id.name", string="XML Filename"
    )

    @api.model
    def _get_declaration_type(self):
        res = []
        company = self.env.company
        arrivals = company.intrastat_arrivals
        dispatches = company.intrastat_dispatches
        if arrivals != "exempt":
            res.append(("arrivals", _("Declaration for Arrivals")))
        if dispatches != "exempt":
            res.append(("dispatches", _("Declaration for Dispatches")))
        return res

    @api.model
    def _get_reporting_level(self):
        return [("standard", _("Standard")), ("extended", _("Extended"))]

    @api.model
    def _get_action(self):
        return [
            ("replace", _("Replace")),
            ("append", _("Append")),
            ("nihil", _("Nihil")),
        ]

    @api.depends("year", "month")
    def _compute_year_month(self):
        for this in self:
            if this.year and this.month:
                this.year_month = "-".join([this.year, this.month])

    @api.constrains("company_id")
    def _check_company_country(self):
        for this in self:
            if not this.company_id.country_id:
                raise ValidationError(
                    _("You must set the country on company '%s'.")
                    % this.company_id.display_name
                )

    @api.depends("declaration_line_ids.amount_company_currency")
    def _compute_numbers(self):
        rg_res = self.env["intrastat.product.declaration.line"].read_group(
            [("parent_id", "in", self.ids)],
            ["parent_id", "amount_company_currency:sum"],
            ["parent_id"],
        )
        mapped_data = {
            x["parent_id"][0]: {
                "num_decl_lines": x["parent_id_count"],
                "total_amount": x["amount_company_currency"],
            }
            for x in rg_res
        }
        for this in self:
            this.num_decl_lines = mapped_data.get(this.id, {}).get("num_decl_lines", 0)
            this.total_amount = mapped_data.get(this.id, {}).get("total_amount", 0)

    @api.constrains("year")
    def _check_year(self):
        for this in self:
            if len(this.year) != 4 or this.year[0] != "2":
                raise ValidationError(_("Invalid Year!"))

    @api.depends("declaration_type", "company_id")
    def _compute_reporting_level(self):
        for this in self:
            reporting_level = False
            if this.declaration_type == "arrivals":
                reporting_level = (
                    this.company_id.intrastat_arrivals == "extended"
                    and "extended"
                    or "standard"
                )
            elif this.declaration_type == "dispatches":
                reporting_level = (
                    this.company_id.intrastat_dispatches == "extended"
                    and "extended"
                    or "standard"
                )
            this.reporting_level = reporting_level

    def copy(self, default=None):
        self.ensure_one()
        default = default or {}
        default["revision"] = self.revision + 1
        return super().copy(default)

    def _account_config_warning(self, msg):
        action = self.env.ref("account.action_account_config")
        raise RedirectWarning(
            msg, action.id, _("Go to Accounting Configuration Settings screen")
        )

    def _attach_xml_file(self, xml_bytes, declaration_name):
        """Attach the XML file to the report_intrastat_product/service
        object"""
        self.ensure_one()
        filename = "{}_{}.xml".format(self.year_month, declaration_name)
        attach = self.env["ir.attachment"].create(
            {
                "name": filename,
                "res_id": self.id,
                "res_model": self._name,
                "raw": xml_bytes,
            }
        )
        return attach.id

    def unlink(self):
        for this in self:
            if this.state == "done":
                raise UserError(
                    _("Cannot delete the declaration %s because it is in Done state.")
                    % this.display_name
                )
        return super().unlink()

    def _get_partner_country(self, inv_line, notedict, eu_countries):
        inv = inv_line.move_id
        country = inv.src_dest_country_id
        if not country:
            line_notes = [
                _(
                    "Missing country on invoice partner '%s' "
                    "or on the delivery address (partner '%s'). "
                )
                % (
                    inv.partner_id.display_name,
                    inv.partner_shipping_id
                    and inv.partner_shipping_id.display_name
                    or "-",
                )
            ]
            self._format_line_note(inv_line, notedict, line_notes)
        else:
            if country not in eu_countries and country.code != "GB":
                line_notes = [
                    _(
                        "On invoice '%s', the source/destination country "
                        "is '%s' which is not part of the European Union."
                    )
                    % (inv.name, country.name)
                ]
                self._format_line_note(inv_line, notedict, line_notes)
        return country

    def _get_intrastat_transaction(self, inv_line, notedict):
        invoice = inv_line.move_id
        transaction = invoice.intrastat_transaction_id
        fieldmap = {
            "out_invoice": "intrastat_out_invoice_transaction_id",
            "out_refund": "intrastat_out_refund_transaction_id",
            "in_invoice": "intrastat_in_invoice_transaction_id",
            "in_refund": "intrastat_in_refund_transaction_id",
        }
        if not transaction and invoice.move_type in fieldmap:
            # as we have searched with intrastat_fiscal_position = True
            # we should always have a fiscal position on the invoice
            transaction = invoice.fiscal_position_id[fieldmap[invoice.move_type]]
        if not transaction and invoice.move_type in fieldmap:
            transaction = invoice.company_id[fieldmap[invoice.move_type]]
        if not transaction:
            line_notes = [
                _(
                    "No Intrastat Transaction Type on invoice '%s'. "
                    "No Default Intrastat Transaction Type on "
                    "the fiscal position of the invoice (%s), "
                    "nor on the accounting configuration page of the company '%s'. "
                )
                % (
                    invoice.name,
                    invoice.fiscal_position_id.display_name,
                    invoice.company_id.display_name,
                )
            ]
            self._format_line_note(inv_line, notedict, line_notes)
        return transaction

    def _get_weight_and_supplunits(self, inv_line, hs_code, notedict):
        line_qty = inv_line.quantity
        product = inv_line.product_id
        intrastat_unit_id = hs_code.intrastat_unit_id
        source_uom = inv_line.product_uom_id
        weight_uom_categ = self.env.ref("uom.product_uom_categ_kgm")
        kg_uom = self.env.ref("uom.product_uom_kgm")
        self.env["decimal.precision"].precision_get("Stock Weight")
        weight = suppl_unit_qty = 0.0

        if not source_uom:
            line_notes = [_("Missing unit of measure.")]
            self._format_line_note(inv_line, notedict, line_notes)
            return weight, suppl_unit_qty

        if intrastat_unit_id:
            target_uom = intrastat_unit_id.uom_id
            if not target_uom:
                line_notes = [
                    _("Intrastat Code %s:") % hs_code.display_name,
                    _(
                        "Conversion from Intrastat Supplementary Unit '%s' to "
                        "Unit of Measure is not implemented yet."
                    )
                    % intrastat_unit_id.name,
                ]
                self._format_line_note(inv_line, notedict, line_notes)
                return weight, suppl_unit_qty
            if target_uom.category_id == source_uom.category_id:
                suppl_unit_qty = source_uom._compute_quantity(line_qty, target_uom)
            else:
                line_notes = [
                    _(
                        "Conversion from unit of measure '%s' to '%s' "
                        "is not implemented yet."
                    )
                    % (source_uom.name, target_uom.name)
                ]
                self._format_line_note(inv_line, notedict, line_notes)
                return weight, suppl_unit_qty

        if source_uom == kg_uom:
            weight = line_qty
        elif source_uom.category_id == weight_uom_categ:
            weight = source_uom._compute_quantity(line_qty, kg_uom)
        elif source_uom.category_id == product.uom_id.category_id:
            # We suppose that, on product.template,
            # the 'weight' field is per uom_id
            weight = product.weight * source_uom._compute_quantity(
                line_qty, product.uom_id
            )
        else:
            line_notes = [
                _(
                    "Conversion from unit of measure '%s' to 'Kg' "
                    "cannot be done automatically. It is needed for product "
                    "'%s' whose unit of measure is %s."
                )
                % (source_uom.name, product.display_name, product.uom_id.display_name)
            ]
            self._format_line_note(inv_line, notedict, line_notes)
        return weight, suppl_unit_qty

    def _get_region_code(self, inv_line, notedict):
        """May be inherited by localisation modules
        If set, Odoo will use the region code returned by this method
        and will not call _get_region() and leave region_id empty
        """
        return False

    def _get_region(self, inv_line, notedict):
        """
        For supplier invoices/refunds: if the invoice line is linked
        to a stock move, use the destination stock location ;
        otherwise, get the PO (which is linked to a stock location)
        and then get the warehouse.
        It is important to take into account the following scenario:
        I order a qty of 50 kg and my suppliers delivers and invoices 52 kg
        (quite common in some industries where the order qty cannot be exact
        due to some operational constraints) ; in this case, I have a qty of
        2 kg which is not linked to a PO, but it is linked to a stock move.

        For customer invoices/refunds: if the invoice line is linked to a
        stock move, use the source stock location ;
        otherwise, get the sale order, which is linked to the warehouse.

        If none found, get the company's default intrastat region.

        """
        region = False
        move_type = inv_line.move_id.move_type
        if move_type in ("in_invoice", "in_refund"):
            po_line = self.env["purchase.order.line"].search(
                [("invoice_lines", "in", inv_line.id)], limit=1
            )
            if po_line:
                if po_line.move_ids:
                    region = po_line.move_ids[0].location_dest_id.get_intrastat_region()
        elif move_type in ("out_invoice", "out_refund"):
            so_line = self.env["sale.order.line"].search(
                [("invoice_lines", "in", inv_line.id)], limit=1
            )
            if so_line:
                so = so_line.order_id
                region = so.warehouse_id.region_id
        if not region:
            if self.company_id.intrastat_region_id:
                region = self.company_id.intrastat_region_id
        return region

    def _get_transport(self, inv_line, notedict):
        transport = (
            inv_line.move_id.intrastat_transport_id
            or self.company_id.intrastat_transport_id
        )
        if not transport:
            msg = _(
                "The default Intrastat Transport Mode "
                "of the Company is not set, "
                "please configure it first."
            )
            self._account_config_warning(msg)
        return transport

    def _get_incoterm(self, inv_line, notedict):
        incoterm = inv_line.move_id.invoice_incoterm_id or self.company_id.incoterm_id
        if not incoterm:
            msg = _(
                "The default Incoterm "
                "of the Company is not set, "
                "please configure it first."
            )
            self._account_config_warning(msg)
        return incoterm

    def _get_product_origin_country(self, inv_line, notedict):
        origin_country = inv_line.product_id.origin_country_id
        if not origin_country:
            line_notes = [
                _("The country of origin is missing on product '%s'.")
                % inv_line.product_id.display_name
            ]
            self._format_line_note(inv_line, notedict, line_notes)
        return origin_country

    def _get_vat(self, inv_line, notedict):
        vat = False
        inv = inv_line.move_id
        if (
            self.declaration_type == "dispatches"
            and inv_line.move_id.fiscal_position_id.vat_required
        ):
            vat = inv.commercial_partner_id.vat
            if vat:
                if vat.startswith("GB"):
                    line_notes = [
                        _(
                            "VAT number of partner '%s' is '%s'. If this partner "
                            "is from Northern Ireland, his VAT number should be "
                            "updated to his new VAT number starting with 'XI' "
                            "following Brexit. If this partner is from Great Britain, "
                            "maybe the fiscal position was wrong on invoice '%s' "
                            "(the fiscal position was '%s')."
                        )
                        % (
                            inv.commercial_partner_id.display_name,
                            vat,
                            inv.name,
                            inv.fiscal_position_id.display_name,
                        )
                    ]
                    self._format_line_note(inv_line, notedict, line_notes)

            else:
                line_notes = [
                    _("Missing VAT Number on partner '%s'")
                    % inv.commercial_partner_id.display_name
                ]
                self._format_line_note(inv_line, notedict, line_notes)
        return vat

    def _update_computation_line_vals(self, inv_line, line_vals, notedict):
        """placeholder for localization modules"""

    def _handle_invoice_accessory_cost(
        self,
        invoice,
        lines_current_invoice,
        total_inv_accessory_costs_cc,
        total_inv_product_cc,
        total_inv_weight,
    ):
        """
        Affect accessory costs pro-rata of the value
        (or pro-rata of the weight if the goods of the invoice
        have no value)

        This method allows to implement a different logic
        in the localization modules.
        E.g. in Belgium accessory cost should not be added.
        """
        if total_inv_accessory_costs_cc:
            if total_inv_product_cc:
                # pro-rata of the value
                for ac_line_vals in lines_current_invoice:
                    ac_line_vals["amount_accessory_cost_company_currency"] = (
                        total_inv_accessory_costs_cc
                        * ac_line_vals["amount_company_currency"]
                        / total_inv_product_cc
                    )
            elif total_inv_weight:
                # pro-rata of the weight
                for ac_line_vals in lines_current_invoice:
                    ac_line_vals["amount_accessory_cost_company_currency"] = (
                        total_inv_accessory_costs_cc
                        * ac_line_vals["weight"]
                        / total_inv_weight
                    )
            else:
                for ac_line_vals in lines_current_invoice:
                    ac_line_vals[
                        "amount_accessory_cost_company_currency"
                    ] = total_inv_accessory_costs_cc / len(lines_current_invoice)

    def _prepare_invoice_domain(self):
        """
        Complete this method in the localization module
        with the country-specific logic for arrivals and dispatches.
        Cf. l10n_be_intrastat_product_declaration for an example
        The dates are based on account.move,date in stead of invoice_date
        to ensure consistency between intrastat and intracomm tax declaration.
        """
        start_date = date(int(self.year), int(self.month), 1)
        end_date = start_date + relativedelta(day=1, months=+1, days=-1)
        domain = [
            ("date", ">=", start_date),
            ("date", "<=", end_date),
            ("state", "=", "posted"),
            ("intrastat_fiscal_position", "=", True),
            ("company_id", "=", self.company_id.id),
        ]
        if self.declaration_type == "arrivals":
            domain.append(("move_type", "in", ("in_invoice", "in_refund")))
        elif self.declaration_type == "dispatches":
            domain.append(("move_type", "in", ("out_invoice", "out_refund")))
        return domain

    def _is_product(self, invoice_line):
        if invoice_line.product_id and invoice_line.product_id.type in (
            "product",
            "consu",
        ):
            return True
        else:
            return False

    def _gather_invoices_init(self, notedict):
        """placeholder for localization modules"""

    def _format_line_note(self, line, notedict, line_notes):
        indent = 8 * " "
        note = _("Invoice %s, line %s") % (line.move_id.name, notedict["line_nbr"])
        note += ":\n"
        for line_note in line_notes:
            note += indent + line_note
            note += "\n"
        notedict["note"] += note

    def _gather_invoices(self, notedict):
        lines = []
        qty_prec = self.env["decimal.precision"].precision_get(
            "Product Unit of Measure"
        )
        accessory_costs = self.company_id.intrastat_accessory_costs
        eu_countries = self.env.ref("base.europe").country_ids

        self._gather_invoices_init(notedict)
        domain = self._prepare_invoice_domain()
        order = "journal_id, name"
        invoices = self.env["account.move"].search(domain, order=order)

        for invoice in invoices:

            lines_current_invoice = []
            total_inv_accessory_costs_cc = 0.0  # in company currency
            total_inv_product_cc = 0.0  # in company currency
            total_inv_weight = 0.0
            for line_nbr, inv_line in enumerate(
                invoice.invoice_line_ids.filtered(lambda x: not x.display_type), start=1
            ):
                notedict["line_nbr"] = line_nbr
                inv_intrastat_line = invoice.intrastat_line_ids.filtered(
                    lambda r: r.invoice_line_id == inv_line
                )

                if (
                    accessory_costs
                    and inv_line.product_id
                    and inv_line.product_id.is_accessory_cost
                ):
                    acost = invoice.currency_id._convert(
                        inv_line.price_subtotal,
                        self.company_id.currency_id,
                        self.company_id,
                        invoice.date,
                    )
                    total_inv_accessory_costs_cc += acost

                    continue

                if float_is_zero(inv_line.quantity, precision_digits=qty_prec):
                    _logger.info(
                        "Skipping invoice line %s qty %s "
                        "of invoice %s. Reason: qty = 0"
                        % (inv_line.name, inv_line.quantity, invoice.name)
                    )
                    continue

                partner_country = self._get_partner_country(
                    inv_line, notedict, eu_countries
                )
                # When the country is the same as the company's country must be skipped.
                if partner_country == self.company_id.country_id:
                    _logger.info(
                        "Skipping invoice line %s qty %s "
                        "of invoice %s. Reason: partner_country = "
                        "company country"
                        % (inv_line.name, inv_line.quantity, invoice.name)
                    )
                    continue

                if inv_intrastat_line:
                    hs_code = inv_intrastat_line.hs_code_id
                elif inv_line.product_id and self._is_product(inv_line):
                    hs_code = inv_line.product_id.get_hs_code_recursively()
                    if not hs_code:
                        line_notes = [
                            _("Missing Intrastat Code on product %s. ")
                            % (inv_line.product_id.display_name)
                        ]
                        self._format_line_note(inv_line, notedict, line_notes)
                        continue
                else:
                    _logger.info(
                        "Skipping invoice line %s qty %s "
                        "of invoice %s. Reason: no product nor Intrastat Code"
                        % (inv_line.name, inv_line.quantity, invoice.name)
                    )
                    continue

                intrastat_transaction = self._get_intrastat_transaction(
                    inv_line, notedict
                )

                if inv_intrastat_line:
                    weight = inv_intrastat_line.transaction_weight
                    suppl_unit_qty = inv_intrastat_line.transaction_suppl_unit_qty
                else:
                    weight, suppl_unit_qty = self._get_weight_and_supplunits(
                        inv_line, hs_code, notedict
                    )
                total_inv_weight += weight

                sign = invoice.move_type in ("in_invoice", "out_refund") and 1 or -1
                amount_company_currency = sign * inv_line.balance
                total_inv_product_cc += amount_company_currency

                if inv_intrastat_line:
                    product_origin_country = (
                        inv_intrastat_line.product_origin_country_id
                    )
                else:
                    product_origin_country = self._get_product_origin_country(
                        inv_line, notedict
                    )

                region_code = self._get_region_code(inv_line, notedict)
                region = False
                if not region_code:
                    region = self._get_region(inv_line, notedict)

                vat = self._get_vat(inv_line, notedict)

                line_vals = {
                    "parent_id": self.id,
                    "invoice_line_id": inv_line.id,
                    "src_dest_country_id": partner_country.id,
                    "product_id": inv_line.product_id.id,
                    "hs_code_id": hs_code.id,
                    "weight": weight,
                    "suppl_unit_qty": suppl_unit_qty,
                    "amount_company_currency": amount_company_currency,
                    "amount_accessory_cost_company_currency": 0.0,
                    "transaction_id": intrastat_transaction.id,
                    "product_origin_country_id": product_origin_country.id or False,
                    "region_code": region_code,
                    "region_id": region and region.id or False,
                    "vat": vat,
                }

                # extended declaration
                if self.reporting_level == "extended":
                    transport = self._get_transport(inv_line, notedict)
                    line_vals.update({"transport_id": transport.id})

                self._update_computation_line_vals(inv_line, line_vals, notedict)

                if line_vals:
                    lines_current_invoice.append(line_vals)

            self._handle_invoice_accessory_cost(
                invoice,
                lines_current_invoice,
                total_inv_accessory_costs_cc,
                total_inv_product_cc,
                total_inv_weight,
            )

            for line_vals in lines_current_invoice:
                if (
                    not line_vals["amount_company_currency"]
                    and not line_vals["amount_accessory_cost_company_currency"]
                ):
                    inv_line = self.env["account.move.line"].browse(
                        line_vals["invoice_line_id"]
                    )
                    _logger.info(
                        "Skipping invoice line %s qty %s "
                        "of invoice %s. Reason: price_subtotal = 0 "
                        "and accessory costs = 0",
                        inv_line.name,
                        inv_line.quantity,
                        inv_line.move_id.name,
                    )
                    continue
                lines.append(line_vals)

        return lines

    def action_gather(self):
        self.ensure_one()
        self.message_post(body=_("Generate Lines from Invoices"))
        notedict = {
            "note": "",
            "line_nbr": 0,
        }
        # TODO: implement a solution to avoid double warnings
        # e.g. warning on invoice that is repeated for every line
        # or warning on a product that is repeated for every invoice line
        # with that product

        self.computation_line_ids.unlink()
        self.declaration_line_ids.unlink()
        lines = self._gather_invoices(notedict)

        vals = {"note": notedict["note"]}
        if not lines:
            vals["action"] = "nihil"
            vals["note"] += (
                "\n"
                + _("No records found for the selected period !")
                + "\n"
                + _("The Declaration Action has been set to 'nihil'.")
            )
        else:
            vals["computation_line_ids"] = [(0, 0, x) for x in lines]

        self.write(vals)
        if vals["note"]:
            result_view = self.env.ref("intrastat_product.intrastat_result_view_form")
            return {
                "name": _("Generate lines from invoices: results"),
                "view_type": "form",
                "view_mode": "form",
                "res_model": "intrastat.result.view",
                "view_id": result_view.id,
                "target": "new",
                "context": dict(self._context, note=vals["note"]),
                "type": "ir.actions.act_window",
            }

    @api.model
    def _group_line_hashcode_fields(self, computation_line):
        return {
            "country": computation_line.src_dest_country_code,
            "hs_code_id": computation_line.hs_code_id.id or False,
            "intrastat_unit": computation_line.intrastat_unit_id.id or False,
            "transaction": computation_line.transaction_id.id or False,
            "transport": computation_line.transport_id.id or False,
            "region": computation_line.region_code or False,
            "product_origin_country": computation_line.product_origin_country_code,
            "vat": computation_line.vat or False,
        }

    def group_line_hashcode(self, computation_line):
        hc_fields = self._group_line_hashcode_fields(computation_line)
        hashcode = "-".join([str(f) for f in hc_fields.values()])
        return hashcode

    @api.model
    def _prepare_grouped_fields(self, computation_line, fields_to_sum):
        vals = {
            "src_dest_country_code": computation_line.src_dest_country_code,
            "intrastat_unit_id": computation_line.intrastat_unit_id.id,
            "hs_code_id": computation_line.hs_code_id.id,
            "transaction_id": computation_line.transaction_id.id,
            "transport_id": computation_line.transport_id.id,
            "region_code": computation_line.region_code,
            "parent_id": computation_line.parent_id.id,
            "product_origin_country_code": computation_line.product_origin_country_code,
            "amount_company_currency": 0.0,
            "vat": computation_line.vat,
        }
        for field in fields_to_sum:
            vals[field] = 0.0
        return vals

    def _fields_to_sum(self):
        fields_to_sum = ["weight", "suppl_unit_qty"]
        return fields_to_sum

    @api.model
    def _prepare_declaration_line(self, computation_lines):
        fields_to_sum = self._fields_to_sum()
        vals = self._prepare_grouped_fields(computation_lines[0], fields_to_sum)
        for computation_line in computation_lines:
            for field in fields_to_sum:
                vals[field] += computation_line[field]
            vals["amount_company_currency"] += (
                computation_line["amount_company_currency"]
                + computation_line["amount_accessory_cost_company_currency"]
            )
        # round, otherwise odoo with truncate (6.7 -> 6... instead of 7 !)
        for field in fields_to_sum:
            vals[field] = int(round(vals[field]))
        if not vals["weight"]:
            vals["weight"] = 1
        vals["amount_company_currency"] = int(round(vals["amount_company_currency"]))
        return vals

    def generate_declaration(self):
        """generate declaration lines from computation lines"""
        self.ensure_one()
        assert not self.declaration_line_ids
        dl_group = {}
        for cl in self.computation_line_ids:
            hashcode = self.group_line_hashcode(cl)
            if hashcode not in dl_group:
                dl_group[hashcode] = cl
            else:
                dl_group[hashcode] |= cl
        ipdl = self.declaration_line_ids
        for cl_lines in dl_group.values():
            vals = self._prepare_declaration_line(cl_lines)
            declaration_line = ipdl.create(vals)
            cl_lines.write({"declaration_line_id": declaration_line.id})

    def _check_generate_xml(self):
        self.ensure_one()
        if not self.company_id.partner_id.vat:
            raise UserError(
                _("The VAT number is not set for the partner '%s'.")
                % self.company_id.partner_id.display_name
            )

    def generate_xml(self):
        """generate the INTRASTAT Declaration XML file"""
        self.ensure_one()
        if self.xml_attachment_id:
            raise UserError(
                _(
                    "An XML Export already exists for %s. "
                    "To re-generate it, you must first delete it."
                )
                % self.display_name
            )
        self.message_post(body=_("Generate XML Declaration File"))
        self._check_generate_xml()
        xml_bytes = self._generate_xml()
        if xml_bytes:
            attach_id = self._attach_xml_file(
                xml_bytes, "{}_{}".format(self.declaration_type, self.revision)
            )
            self.write({"xml_attachment_id": attach_id})
            return
        else:
            raise UserError(_("No XML File has been generated."))

    def delete_xml(self):
        self.ensure_one()
        self.xml_attachment_id and self.xml_attachment_id.unlink()

    def create_xls(self):
        if self.env.context.get("computation_lines"):
            report_file = "intrastat_transactions"
        else:
            report_file = "intrastat_declaration_lines"
        return {
            "type": "ir.actions.report",
            "report_type": "xlsx",
            "report_name": "intrastat_product.product_declaration_xls",
            "context": dict(self.env.context, report_file=report_file),
            "data": {"dynamic_report": True},
        }

    @api.model
    def _xls_computation_line_fields(self):
        """
        Update list in custom module to add/drop columns or change order
        """
        return [
            "product",
            "hs_code",
            "src_dest_country",
            "src_dest_country_code",
            "amount_company_currency",
            "accessory_cost",
            "transaction",
            "weight",
            "suppl_unit_qty",
            "suppl_unit",
            "transport",
            "region",
            "region_code",
            "product_origin_country",
            "product_origin_country_code",
            "vat",
            "partner_id",
            "invoice",
        ]

    @api.model
    def _xls_declaration_line_fields(self):
        """
        Update list in custom module to add/drop columns or change order
        Use same order as tree view by default
        """
        return [
            "hs_code",
            "src_dest_country_code",
            "amount_company_currency",
            "transaction_code",
            "transaction",
            "weight",
            "suppl_unit_qty",
            "suppl_unit",
            "transport_code",
            "transport",
            "region_code",
            "product_origin_country_code",
            "vat",
        ]

    @api.model
    def _xls_template(self):
        """
        Placeholder for excel report template updates

        """
        return {}

    def done(self):
        for decl in self:
            decl.generate_declaration()
            decl.generate_xml()
        self.write({"state": "done"})

    def back2draft(self):
        for decl in self:
            decl.delete_xml()
            decl.declaration_line_ids.unlink()
        self.write({"state": "draft"})


class IntrastatProductComputationLine(models.Model):
    _name = "intrastat.product.computation.line"
    _description = "Intrastat Product Computataion Lines"

    parent_id = fields.Many2one(
        "intrastat.product.declaration",
        string="Intrastat Product Declaration",
        ondelete="cascade",
        readonly=True,
    )
    company_id = fields.Many2one(related="parent_id.company_id")
    company_currency_id = fields.Many2one(
        related="company_id.currency_id", string="Company currency"
    )
    company_country_code = fields.Char(
        related="parent_id.company_id.country_id.code", string="Company Country Code"
    )
    declaration_type = fields.Selection(related="parent_id.declaration_type")
    reporting_level = fields.Selection(related="parent_id.reporting_level")
    invoice_line_id = fields.Many2one(
        "account.move.line", string="Invoice Line", readonly=True
    )
    invoice_id = fields.Many2one(
        "account.move", related="invoice_line_id.move_id", string="Invoice"
    )
    partner_id = fields.Many2one(
        related="invoice_line_id.move_id.commercial_partner_id", string="Partner"
    )
    declaration_line_id = fields.Many2one(
        "intrastat.product.declaration.line", string="Declaration Line", readonly=True
    )
    src_dest_country_id = fields.Many2one(
        "res.country",
        string="Country",
        help="Country of Origin/Destination",
    )
    src_dest_country_code = fields.Char(
        compute="_compute_src_dest_country_code",
        string="Country Code",
        required=True,
        readonly=False,
        help="2 digit code of country of origin/destination.\n"
        "Specify 'XI' for UK Northern Ireland.",
    )
    product_id = fields.Many2one(
        "product.product", related="invoice_line_id.product_id"
    )
    hs_code_id = fields.Many2one("hs.code", string="Intrastat Code")
    intrastat_unit_id = fields.Many2one(
        "intrastat.unit",
        related="hs_code_id.intrastat_unit_id",
        string="Suppl. Unit",
        help="Intrastat Supplementary Unit",
    )
    weight = fields.Float(
        string="Weight", digits="Stock Weight", help="Net weight in Kg"
    )
    suppl_unit_qty = fields.Float(
        string="Suppl. Unit Qty",
        digits="Product Unit of Measure",
        help="Supplementary Units Quantity",
    )
    amount_company_currency = fields.Float(
        string="Fiscal Value",
        digits="Account",
        required=True,
        help="Amount in company currency to write in the declaration. "
        "Amount in company currency = amount in invoice currency "
        "converted to company currency with the rate of the invoice date.",
    )
    amount_accessory_cost_company_currency = fields.Float(
        string="Accessory Costs",
        digits="Account",
        help="Amount in company currency of the accessory costs related to "
        "this invoice line (by default, these accessory costs are computed "
        "at the pro-rata of the amount of each invoice line.",
    )
    transaction_id = fields.Many2one(
        "intrastat.transaction",
        string="Intrastat Transaction",
    )
    region_id = fields.Many2one("intrastat.region", string="Intrastat Region")
    # Note that, in l10n_fr_intrastat_product and maybe in other localization modules
    # region_id is left empty and Odoo writes directly in region_code
    region_code = fields.Char()
    product_origin_country_id = fields.Many2one(
        "res.country",
        string="Country of Origin of the Product",
        help="Country of origin of the product i.e. product 'made in ____'",
    )
    product_origin_country_code = fields.Char(
        compute="_compute_product_origin_country_code",
        string="Country Code of Origin of the Product",
        size=2,
        required=True,
        readonly=False,
        help="2 digit ISO code of the country of origin of the product.\n"
        "Specify 'QU' when the country of origin is unknown.\n",
    )
    vat = fields.Char(string="VAT Number")

    # extended declaration
    incoterm_id = fields.Many2one("account.incoterms", string="Incoterm")
    transport_id = fields.Many2one("intrastat.transport_mode", string="Transport Mode")

    @api.onchange("region_id")
    def _region_id_change(self):
        if self.region_id:
            self.region_code = self.region_id.code

    @api.depends("src_dest_country_id")
    def _compute_src_dest_country_code(self):
        for this in self:
            code = this.src_dest_country_id and this.src_dest_country_id.code or False
            if code == "GB":
                code = "XI"  # Northern Ireland
            this.src_dest_country_code = code

    @api.depends("product_origin_country_id")
    def _compute_product_origin_country_code(self):
        for this in self:
            code = (
                this.product_origin_country_id
                and this.product_origin_country_id.code
                or False
            )
            if code == "GB":
                code = "XU"
                # XU can be used when you don't know if the product
                # originate from Great-Britain or from Northern Ireland
            this.product_origin_country_code = code

    @api.constrains("vat")
    def _check_vat(self):
        for this in self:
            if this.vat and not is_valid(this.vat):
                raise ValidationError(_("The VAT number '%s' is invalid.") % this.vat)


class IntrastatProductDeclarationLine(models.Model):
    _name = "intrastat.product.declaration.line"
    _description = "Intrastat Product Declaration Lines"

    parent_id = fields.Many2one(
        "intrastat.product.declaration",
        string="Intrastat Product Declaration",
        ondelete="cascade",
        readonly=True,
    )
    company_id = fields.Many2one(related="parent_id.company_id")
    company_currency_id = fields.Many2one(
        related="company_id.currency_id", string="Company currency"
    )
    company_country_code = fields.Char(
        related="parent_id.company_id.country_id.code", string="Company Country Code"
    )
    declaration_type = fields.Selection(related="parent_id.declaration_type")
    reporting_level = fields.Selection(related="parent_id.reporting_level")
    computation_line_ids = fields.One2many(
        "intrastat.product.computation.line",
        "declaration_line_id",
        string="Computation Lines",
        readonly=True,
    )
    src_dest_country_code = fields.Char(
        string="Country Code",
        required=True,
        help="2 digit code of country of origin/destination.\n"
        "Specify 'XI' for UK Northern Ireland and 'XU' for rest of the UK.",
    )
    hs_code_id = fields.Many2one("hs.code", string="Intrastat Code")
    intrastat_unit_id = fields.Many2one(
        "intrastat.unit",
        related="hs_code_id.intrastat_unit_id",
        string="Suppl. Unit",
        help="Intrastat Supplementary Unit",
    )
    weight = fields.Integer(string="Weight", help="Net weight in Kg")
    suppl_unit_qty = fields.Integer(
        string="Suppl. Unit Qty", help="Supplementary Units Quantity"
    )
    amount_company_currency = fields.Integer(
        string="Fiscal Value",
        help="Amount in company currency to write in the declaration. "
        "Amount in company currency = amount in invoice currency "
        "converted to company currency with the rate of the invoice date.",
    )
    transaction_id = fields.Many2one(
        "intrastat.transaction", string="Intrastat Transaction"
    )
    region_code = fields.Char()
    product_origin_country_code = fields.Char(
        string="Country of Origin of the Product",
        size=2,
        required=True,
        default="QU",
        help="2 digit code of country of origin of the product except for the UK.\n"
        "Specify 'XI' for UK Northern Ireland and 'XU' for rest of the UK.\n"
        "Specify 'QU' when the country is unknown.\n",
    )
    vat = fields.Char(string="VAT Number")
    # extended declaration
    incoterm_id = fields.Many2one("account.incoterms", string="Incoterm")
    transport_id = fields.Many2one("intrastat.transport_mode", string="Transport Mode")
