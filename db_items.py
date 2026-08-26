"""
قراءة قائمة الأصناف مباشرة من قاعدة بيانات AccSystem (Oracle) بدل الاعتماد
على ملف مُصدَّر يدوياً قد يصبح قديماً. يقرأ بيانات الاتصال الحقيقية من نفس
ملف إعدادات AccSystem (AccSystem.exe.config)، تماماً كما يفعل البرنامج نفسه.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import oracledb

import config
from items import ReferenceItem


class DbConnectionError(RuntimeError):
    pass


def _parse_connection_string(config_path: Path, connection_name: str) -> dict:
    if not config_path.exists():
        raise DbConnectionError(f"ملف إعدادات AccSystem غير موجود: {config_path}")

    try:
        tree = ET.parse(config_path)
    except ET.ParseError as exc:
        raise DbConnectionError(f"ملف إعدادات AccSystem تالف أو غير قابل للقراءة: {config_path} ({exc})") from exc
    root = tree.getroot()

    conn_str = None
    for add_el in root.findall(".//connectionStrings/add"):
        if add_el.get("name") == connection_name:
            conn_str = add_el.get("connectionString")
            break

    if not conn_str:
        raise DbConnectionError(
            f"لم يتم العثور على سطر اتصال باسم '{connection_name}' داخل {config_path}"
        )

    parts = {}
    for chunk in conn_str.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        parts[key.strip().lower()] = value.strip()

    return {
        "user": parts.get("user id") or parts.get("uid") or parts.get("user"),
        "password": parts.get("password") or parts.get("pwd"),
        "data_source": parts.get("data source") or parts.get("server"),
    }


def _find_tnsnames_dir() -> str | None:
    """يبحث عن مجلد يحتوي tnsnames.ora بجانب تثبيت AccSystem (لحل اسم TNS)."""
    install_dir = config.ACCSYSTEM_CONFIG_PATH.parent
    candidates = [
        install_dir / "Oracle19" / "network" / "admin",
        install_dir / "Oracle10" / "network" / "admin",
        install_dir / "network" / "admin",
        install_dir,
    ]
    for candidate in candidates:
        if (candidate / "tnsnames.ora").exists():
            return str(candidate)
    return None


def _resolve_dsn(data_source: str) -> tuple[str, str | None]:
    """يرجع (dsn, config_dir). إن كان data_source عنوان EZConnect يُستخدم مباشرة،
    وإلا يُعامَل كاسم TNS ويُبحث عن tnsnames.ora لحله."""
    looks_like_ezconnect = ":" in data_source or "/" in data_source
    if looks_like_ezconnect:
        return data_source, None

    tns_dir = _find_tnsnames_dir()
    if tns_dir is None:
        raise DbConnectionError(
            f"اسم الاتصال في AccSystem.exe.config هو '{data_source}' (اسم TNS وليس "
            "عنواناً مباشراً)، ولم يتم إيجاد ملف tnsnames.ora بجانب تثبيت AccSystem "
            "لحل هذا الاسم. استخدم زر \"عنوان سيرفر قاعدة البيانات\" داخل الأداة "
            "وضع العنوان يدوياً بصيغة 'host:port/service_name'."
        )
    return data_source, tns_dir


_COMPANY_SCHEMA_RE = re.compile(r"^SAL_\d{4}_\d+$")


def _connect():
    override_user, override_password = config.get_oracle_credentials()
    if override_user:
        user = override_user
        password = override_password
        dsn = config.get_dsn()
        tns_config_dir = None
    else:
        creds = _parse_connection_string(
            config.ACCSYSTEM_CONFIG_PATH, config.ACCSYSTEM_DB_CONNECTION_NAME
        )
        if not creds["user"] or not creds["data_source"]:
            raise DbConnectionError(
                "بيانات اتصال قاعدة البيانات في AccSystem.exe.config غير مكتملة أو ما زالت "
                "قيماً افتراضية (وهمية) — تحقق منها يدوياً أو استخدم ملف الأصناف المُصدَّر بدلاً من ذلك."
            )
        user, password = creds["user"], creds["password"]
        dsn, tns_config_dir = _resolve_dsn(creds["data_source"])

    try:
        connect_kwargs = {"user": user, "password": password, "dsn": dsn}
        if tns_config_dir:
            connect_kwargs["config_dir"] = tns_config_dir
        return oracledb.connect(**connect_kwargs)
    except oracledb.Error as exc:
        raise DbConnectionError(f"تعذّر الاتصال بقاعدة بيانات AccSystem: {exc}") from exc


def list_company_schemas() -> list[str]:
    """
    يرجع كل أسماء schemas الشركات/الملفات المتاحة بقاعدة البيانات (نمط
    SAL_<سنة>_<رقم>)، مرتبة من الأحدث إنشاءً. بعض العملاء عندهم أكثر من
    ملف/شركة نشطة بنفس الوقت، فيُترك اختيار أيهم للمستخدم بدل التخمين.
    """
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT username FROM all_users WHERE username LIKE 'SAL%' ORDER BY created DESC")
            return [row[0] for row in cursor.fetchall() if _COMPANY_SCHEMA_RE.match(row[0])]
    finally:
        connection.close()


def load_item_reference_from_db(schema: str | None = None) -> list[ReferenceItem]:
    """يحمّل أصناف schema مُحدَّد. إن تُرك schema فارغاً: يُستخدم
    config.ORACLE_ITEMS_SCHEMA إن كان محدَّداً، وإلا يُكتشف تلقائياً (يفترض
    وجود شركة واحدة فقط - استخدم list_company_schemas() أولاً لو قد يكون
    هناك أكثر من شركة، بدل ترك هذي الدالة تخمّن)."""
    connection = _connect()

    if not schema:
        schema = config.ORACLE_ITEMS_SCHEMA
    if not schema:
        candidates = list_company_schemas()
        if not candidates:
            connection.close()
            raise DbConnectionError(
                "لم يتم إيجاد أي schema بنمط شركة (SAL_سنة_رقم) بقاعدة البيانات. "
                "حدّد الاسم يدوياً في config.ORACLE_ITEMS_SCHEMA."
            )
        schema = candidates[0]

    # التحقق هنا صراحة (وليس فقط بجهة الاستدعاء) - schema يُدرج مباشرة
    # بنص استعلام SQL (لا يوجد bind variable لاسم schema/جدول بأوراكل)، فلازم
    # يُتحقق من صيغته هنا بالضبط عند نقطة البناء الفعلية، مو نعتمد إن كل
    # الاستدعاءات الحالية والمستقبلية بتلتزم بنفس التحقق المسبق.
    if not _COMPANY_SCHEMA_RE.match(schema):
        raise DbConnectionError(f"اسم schema غير صالح: '{schema}'")

    query = config.ITEMS_TABLE_QUERY.format(schema=f"{schema}.")

    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    except oracledb.Error as exc:
        raise DbConnectionError(f"تعذّر قراءة جدول الأصناف: {exc}") from exc
    finally:
        connection.close()

    items = []
    for cls_id, cls_no, cls_arname, cls_enname, cls_un_1, un_barcode_no, un_id in rows:
        code = str(cls_no).strip() if cls_no is not None else ""
        name = (cls_arname or cls_enname or "").strip() if (cls_arname or cls_enname) else ""
        if not code or not name:
            continue
        barcode = str(un_barcode_no).strip() if un_barcode_no else ""
        default_unit = str(cls_un_1).strip() if cls_un_1 else ""
        internal_id = str(cls_id).strip() if cls_id is not None else ""
        unit_id = str(un_id).strip() if un_id is not None else ""
        items.append(
            ReferenceItem(
                code=code,
                name=name,
                barcode=barcode,
                default_unit=default_unit,
                internal_id=internal_id,
                unit_id=unit_id,
            )
        )
    return items
