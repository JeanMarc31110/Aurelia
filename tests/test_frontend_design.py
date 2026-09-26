import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"


class FrontendDesignTests(unittest.TestCase):
    def test_authenticated_pages_share_the_application_shell(self):
        pages = {
            "dashboard.html", "invoice_list.html", "invoice_detail.html",
            "work_queue.html",
            "document_detail.html", "bank.html", "payments.html",
            "accounting_exports.html", "emails.html", "company.html",
            "supplier_banks.html", "ocr_settings.html",
        }
        for name in pages:
            source = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertIn('{% extends "base.html" %}', source, name)

    def test_authentication_pages_share_the_local_auth_shell(self):
        for name in ("login.html", "setup.html", "change_password.html"):
            source = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertIn('{% extends "auth_base.html" %}', source, name)
            self.assertIn("<label", source, name)

    def test_primary_navigation_uses_only_existing_routes(self):
        source = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        for route in (
            '/', '/work', '/invoices', '/emails', '/payments', '/bank',
            '/exports/accounting', '/settings/company',
            '/settings/supplier-banks', '/settings/ocr', '/logout',
        ):
            self.assertIn(f'href="{route}"', source)
        self.assertIn('aria-label="Navigation principale"', source)

    def test_primary_navigation_uses_clear_customer_labels(self):
        source = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        for label in (
            "Tableau de bord", "À traiter", "Factures", "Rapprochements", "Banque",
            "Échéances", "Exports", "Entreprise", "Paramètres",
        ):
            self.assertIn(f"<span>{label}</span>", source)

    def test_business_statuses_are_centralized_and_translated(self):
        source = (TEMPLATES / "ui_macros.html").read_text(encoding="utf-8")
        for code, label in (
            ("UNPAID", "Impayée"), ("PAID", "Payée"),
            ("APPROVED", "Validée"), ("REJECTED", "Rejetée"),
            ("REVIEW_REQUIRED", "À vérifier"), ("PENDING", "En attente"),
            ("DUPLICATE", "Doublon"), ("ERROR", "Erreur"),
        ):
            self.assertIn(f"'{code}':'{label}'", source)

    def test_frontend_has_no_remote_runtime_dependency(self):
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [*TEMPLATES.glob("*.html"), *STATIC.glob("*")]
            if path.is_file()
        )
        self.assertIsNone(re.search(r"https?://|//cdn\.|fonts\.googleapis", sources, re.I))

    def test_design_system_and_responsive_breakpoints_are_centralized(self):
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        for token in ("--navy-950", "--success", "--warning", "--danger", "--radius", "--sidebar-width"):
            self.assertIn(token, css)
        self.assertIn("@media(max-width:900px)", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_dark_theme_is_declared_on_app_and_auth_shells(self):
        for name in ("base.html", "auth_base.html"):
            source = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertIn('data-theme="aurelia-dark"', source)
            self.assertIn('name="color-scheme" content="dark"', source)

    def test_core_ux_copy_and_primary_actions_are_explicit(self):
        setup = (TEMPLATES / "setup.html").read_text(encoding="utf-8")
        dashboard = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
        invoice = (TEMPLATES / "invoice_detail.html").read_text(encoding="utf-8")
        self.assertIn("Aurélia transforme vos documents en données comptables prêtes à valider", setup)
        self.assertIn("Importer</li>", setup)
        self.assertIn("Configurer Aurélia", setup)
        self.assertEqual(dashboard.count(">Importer un document<"), 1)
        self.assertIn("À traiter aujourd’hui", dashboard)
        self.assertIn('class="invoice-decision-bar"', invoice)
        for action in ("Valider", "Corriger", "Rejeter"):
            self.assertIn(f">{action}</a>", invoice)


if __name__ == "__main__":
    unittest.main()
