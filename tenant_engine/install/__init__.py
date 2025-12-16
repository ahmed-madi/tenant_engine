from __future__ import unicode_literals

import frappe
from frappe.installer import update_site_config
from frappe.utils import get_bench_path, get_site_path, cint
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
from frappe.geo.country_info import get_country_info

from pathlib import Path

import json
import os


def before_install():
    frappe.flags.skip_saas = True


def after_install():
    site_name = get_site_path().split("/")
    configs = {}
    if len(site_name) > 1:
        configs = read_saas_config(site_name[1])
    
    if not configs:
        return

    set_company_and_users(configs)
    install_basic_docs()
    enable_currencies()
    set_login_page_as_default_home_page()
    check_enabled_features()
    disable_signup()
    frappe.db.commit()
    update_site_config("main_password", "")
    frappe.flags.skip_saas = False
    frappe.db.set_value(
        "Stock Settings", "Stock Settings", "valuation_method", "Moving Average"
    )
    frappe.db.commit()
    delete_from_party_type()
    add_oauth(configs)


def enable_currencies():
    frappe.db.sql("UPDATE `tabCurrency` SET enabled=1")


def dont_skip_setup_wizerd():
    frappe.db.set_value("System Settings", "System Settings", "setup_complete", 0)
    frappe.db.set_value("System Settings", "System Settings", "is_first_startup", 1)


def skip_setup_wizerd():
    # For new site use this defaults values
    frappe.db.set_default("desktop:home_page", "")
    frappe.db.set_value("System Settings", "System Settings", "setup_complete", 1)
    frappe.db.set_value("System Settings", "System Settings", "is_first_startup", 0)


def read_saas_config(site_name):
    """Update a value in site_config"""

    site_config = {}
    site_config_path = os.path.join(
        get_bench_path(), "sites", f"{site_name}.config.json"
    )

    _file = Path(site_config_path)

    if not _file.is_file():
        _file.touch()
        with open(site_config_path, "w") as f:
            f.write(json.dumps({}, indent=1, sort_keys=True))

    with open(_file, "r") as f:
        data = f.read()
        frappe.log_error(title="config-content", message=data)
        site_config = json.loads(data)

    return site_config


def set_company_and_users(configs):

    address = configs.get("business_address", {})
    country = address.get("country", "")
    company = configs.get("business_details", {}).get("business_legal_name", "")
    users = [
        {"user_type": "support", "data": configs.get("support_user", {})},
        {"user_type": "owner", "data": configs.get("business_user", {})},
    ]

    yar = frappe.utils.get_year_start(frappe.utils.nowdate()).year
    country_info = get_country_info(country)
    args = {
        "country": country,
        "currency": country_info.get("currency", "USD"),
        "company_name": company,
        "company_tagline": company,
        "language": "English",
        "timezone": country_info.get("timezones", ["Europe/Kiev"])[0],
        "date_format": country_info.get("date_format", "dd-mm-yyyy"),
        "time_format": country_info.get("time_format", "HH:mm:ss"),
        "domains": ["Services"],
        "bank_account": "Bank Name",
        "chart_of_accounts": "Standard",
        "fy_start_date": f"{yar}-01-01",
        "fy_end_date": f"{yar}-12-31",
    }
    setup_complete(args)
    frappe.db.commit()
    # add users
    for user in users:
        create_user(user)
    # create Addresss => use company as address # title
    create_address(company, address)
    skip_setup_wizerd()


def create_user(user_data):
    try:
        new_user = frappe.new_doc("User")
        user_type = user_data.get("user_type", "")
        data = user_data.get("data", {})
        roles = []
        # business_user
        if user_type == "owner":
            roles = [
                role[0]
                for role in frappe.db.sql(
                    "SELECT name FROM `tabRole` WHERE role_name LIKE '%Manager%'"
                )
            ]
            new_user.update(
                {
                    "email": data.get("email_address", ""),
                    "first_name": data.get("first_name", ""),
                    "last_name": data.get("last_name", ""),
                    "birth_date": data.get("date_of_birth", ""),
                    "mobile_no": data.get("phone_number", ""),
                    "new_password": data.get("password", ""),
                    "send_welcome_email": 0,
                }
            )
        elif user_type == "support":
            roles = [role[0] for role in frappe.db.sql("SELECT name FROM `tabRole`")]
            new_user.update(
                {
                    "email": data.get("support_email", ""),
                    "first_name": data.get("support_name", ""),
                    "new_password": data.get("password", ""),
                    "send_welcome_email": 0,
                }
            )

        # create user
        new_user.flags.no_welcome_mail = True
        new_user.flags.ignore_password_policy = True
        new_user.save()
        frappe.db.commit()
        # force set password
        new_user.new_password = data.get("password", "")
        new_user.save()
        frappe.db.commit()

        # Add roles
        for role in roles:
            new_user.add_roles(role)
        new_user.save()
        frappe.db.commit()

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Error while creating users")


def set_login_page_as_default_home_page():
    frappe.db.set_value("Website Settings", "Website Settings", "home_page", "login")
    frappe.db.commit()


def check_enabled_features():
    # Check Stock/Apis Feature
    property_list = ["default", "hidden"]
    # Check maintain stock field Features
    for row in property_list:
        property_setter = frappe.db.exists(
            "Property Setter",
            {"doc_type": "Item", "field_name": "is_stock_item", "property": row},
        )
        if property_setter:
            frappe.db.set_value(
                "Property Setter",
                property_setter,
                "value",
                frappe.conf.get("enable_stock"),
            )
        else:
            if not frappe.conf.get("enable_stock"):
                # Create Property Setter to Change Default Value From 1 to 0 & hide maintain stock field
                frappe.get_doc(
                    {
                        "doctype": "Property Setter",
                        "doctype_or_field": "DocField",
                        "doc_type": "Item",
                        "field_name": "is_stock_item",
                        "property": row,
                        "property_type": "Text" if row == "default" else "Check",
                        "value": "0",
                    }
                ).insert(ignore_permissions=True)


def update_custom_roles(role_args, args):
    name = frappe.db.get_value("Custom Role", role_args, "name")
    if name:
        custom_role = frappe.get_doc("Custom Role", name)
        for new_role in args.get("roles", []):
            rol = new_role.get("role", False)
            if not rol:
                continue
            custom_role.append("roles", {"role": rol})
        custom_role.save(ignore_permissions=True)
    else:
        frappe.get_doc(args).insert(ignore_permissions=True)


def disable_signup():
    frappe.db.set_value("Website Settings", "Website Settings", "disable_signup", 1)
    frappe.db.commit()


def delete_from_party_type():
    party_type_list = frappe.get_list("Party Type")
    for party_type in party_type_list:
        if party_type.name not in ["Customer", "Supplier", "Employee"]:
            doc = frappe.get_doc("Party Type", party_type.name)
            doc.delete()
    frappe.db.commit()


def create_address(company, data):
    address_line_1 = data.get("address_line_1")
    city = data.get("city")
    country = data.get("country")

    if not address_line_1 or not city or not country:
        return
    try:
        address = frappe.new_doc("Address")
        address.address_title = company
        address.address_type = "Office"
        address.address_line1 = data.get("address_line_1")
        address.address_line_2 = data.get("address_line_2")
        address.city = data.get("city")
        address.country = data.get("country")
        address.pincode = data.get("zip_code")
        address.is_your_company_address = 1
        address.append("links", {"link_doctype": "Company", "link_name": company})
        address.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            frappe.get_traceback(), "Error while creating Address" + str(e)
        )

def add_oauth(configs):
    if not configs:
        return
    
    client_auth = configs.get("client_auth", {})
    if cint(client_auth.get("enable_oauth")) == 0:
        return
    social = frappe.new_doc("Social Login Key")
    social.enable_social_login = 1
    social.social_login_provider = "Frappe"
    social.provider_name = "Frappe"
    base_url = client_auth.get("base_url")
    if not base_url:
        return
    social.base_url = base_url

    client_id = client_auth.get("client_id")
    if not client_id:
        return
    social.client_id = client_id
    client_secret = client_auth.get("client_secret")
    if not client_secret:
        return
    social.client_secret = client_secret
    social.authorize_url = "/api/method/frappe.integrations.oauth2.authorize"
    social.redirect_url = "/api/method/frappe.integrations.oauth2_logins.login_via_frappe"
    social.access_token_url = "/api/method/frappe.integrations.oauth2.get_token"
    social.api_endpoint = "/api/method/frappe.integrations.oauth2.openid_profile"
    social.auth_url_data = '{"response_type": "code", "scope": "openid"}'
    social.flags.ignore_permissions = True
    social.save()
    frappe.db.set_value("System Settings", "System Settings", "disable_user_pass_login", 1)
    frappe.db.commit()