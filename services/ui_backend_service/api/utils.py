from urllib.parse import urlsplit, parse_qsl
from multidict import MultiDict
from aiohttp import web
from typing import Callable, List, Dict
from services.data.db_utils import DBResponse
from services.utils import format_qs, format_baseurl, web_response


def format_response(request: web.BaseRequest, db_response: DBResponse) -> (int, Dict):
    query = {}
    for key in request.query:
        query[key] = request.query.get(key)

    baseurl = format_baseurl(request)
    response_object = {
        "data": db_response.body,
        "status": db_response.response_code,
        "links": {
            "self": "{}{}".format(baseurl, format_qs(query))
        },
        "query": query,
    }
    return db_response.response_code, response_object


def format_response_list(request: web.BaseRequest, db_response: DBResponse, page: int, lastPage: int) -> (int, Dict):
    query = {}
    for key in request.query:
        query[key] = request.query.get(key)

    nextPage = min(page + 1, lastPage)
    prevPage = max(page - 1, 1)

    baseurl = format_baseurl(request)
    response_object = {
        "data": db_response.body,
        "status": db_response.response_code,
        "links": {
            "self": "{}{}".format(baseurl, format_qs(query)),
            "first": "{}{}".format(baseurl, format_qs(query, {"_page": 1})),
            "prev": "{}{}".format(baseurl, format_qs(query, {"_page": prevPage})),
            "next": "{}{}".format(baseurl, format_qs(query, {"_page": nextPage})),
            "last": "{}{}".format(baseurl, format_qs(query, {"_page": lastPage}))
        },
        "pages": {
            "self": page,
            "first": 1,
            "prev": prevPage,
            "next": nextPage,
            "last": lastPage
        },
        "query": query,
    }
    return db_response.response_code, response_object


def pagination_query(request: web.BaseRequest, allowed_order: List[str] = [], allowed_group: List[str] = []):
    # Page
    try:
        page = max(int(request.query.get("_page", 1)), 1)
    except:
        page = 1

    # Limit
    try:
        # Default limit is 10, maximum is 1000
        limit = min(int(request.query.get("_limit", 10)), 1000)
    except:
        limit = 10

    # Group limit
    try:
        # default rows per group 10. Cap at 1000
        group_limit = min(int(request.query.get("_group_limit", 10)), 1000)
    except:
        group_limit = 10

    # Offset
    offset = limit * (page - 1)

    # Order by
    try:
        _order = request.query.get("_order")
        if _order is not None:
            _orders = []
            for order in _order.split(","):
                if order.startswith("+"):
                    column = order[1:]
                    direction = "ASC"
                elif order.startswith("-"):
                    column = order[1:]
                    direction = "DESC"
                else:
                    column = order
                    direction = "DESC"

                if column in allowed_order:
                    _orders.append("{} {}".format(column, direction))

            order = _orders
        else:
            order = None

    except:
        order = None

    # Grouping (partitioning)
    # Allows single or multiple grouping rules (nested grouping)
    # Limits etc. will be applied to each group
    _group = request.query.get("_group")
    if _group is not None:
        groups = []
        for g in _group.split(","):
            if g in allowed_group:
                groups.append(g)
    else:
        groups = None

    return page, limit, offset, \
        order if order else None, \
        groups if groups else None, \
        group_limit


# Built-in conditions (always prefixed with _)
def builtin_conditions_query(request: web.BaseRequest):
    return builtin_conditions_query_dict(request.query)


def builtin_conditions_query_dict(query: MultiDict):
    conditions = []
    values = []

    for key, val in query.items():
        if not key.startswith("_"):
            continue

        deconstruct = key.split(":", 1)
        if len(deconstruct) > 1:
            field = deconstruct[0]
            operator = deconstruct[1]
        else:
            field = key
            operator = None

        # Tags
        if field == "_tags":
            tags = val.split(",")
            if operator == "likeany" or operator == "likeall":
                # `?_tags:likeany` => LIKE ANY (OR)
                # `?_tags:likeall` => LIKE ALL (AND)
                # Raw SQL: SELECT * FROM runs_v3 WHERE tags||system_tags::text LIKE ANY(array['{%runtime:dev%','%user:m%']');
                # Psycopg SQL: SELECT * FROM runs_v3 WHERE tags||system_tags::text LIKE ANY(array[%s,%s]);
                # Values for Psycopg: ['%runtime:dev%','%user:m%']
                compare = "ANY" if operator == "likeany" else "ALL"

                conditions.append(
                    "tags||system_tags::text LIKE {}(array[{}])"
                    .format(compare, ",".join(["%s"]*len(tags))))
                values += map(lambda t: "%{}%".format(t), tags)

            else:
                # `?_tags:any` => ?| (OR)
                # `?_tags:all` => ?& (AND) (default)
                compare = "?|" if operator == "any" else "?&"

                conditions.append("tags||system_tags {} array[{}]".format(
                    compare, ",".join(["%s"]*len(tags))))
                values += tags

    return conditions, values


operators_to_sql = {
    "eq": "{} = %s",          # equals
    "ne": "{} != %s",         # not equals
    "lt": "{} < %s",          # less than
    "le": "{} <= %s",         # less than or equals
    "gt": "{} > %s",          # greater than
    "ge": "{} >= %s",         # greater than or equals
    "co": "{} ILIKE %s",      # contains
    "sw": "{} ILIKE %s",      # starts with
    "ew": "{} ILIKE %s",      # ends with
}

operators_to_sql_values = {
    "eq": "{}",
    "ne": "{}",
    "lt": "{}",
    "le": "{}",
    "gt": "{}",
    "ge": "{}",
    "co": "%{}%",
    "sw": "{}%",
    "ew": "%{}",
}


# Custom conditions parser (table columns, never prefixed with _)
def custom_conditions_query(request: web.BaseRequest, allowed_keys: List[str] = []):
    return custom_conditions_query_dict(request.query, allowed_keys)


def custom_conditions_query_dict(query: MultiDict, allowed_keys: List[str] = []):
    conditions = []
    values = []

    for key, val in query.items():
        if key.startswith("_"):
            continue

        deconstruct = key.split(":", 1)
        if len(deconstruct) > 1:
            field = deconstruct[0]
            operator = deconstruct[1]
        else:
            field = key
            operator = "eq"

        if allowed_keys is not None and field not in allowed_keys:
            continue

        if operator not in operators_to_sql:
            continue

        vals = val.split(",")

        conditions.append(
            "({})".format(" OR ".join(
                map(lambda _: operators_to_sql[operator].format(field), vals)
            ))
        )
        values += map(
            lambda v: operators_to_sql_values[operator].format(v), vals)

    return conditions, values


# Parse path, query params, SQL conditions and values from URL
#
# Example:
#   /runs?flow_id=HelloFlow&status=running
#
#   -> Path: /runs
#   -> Query: MultiDict('flow_id': 'HelloFlow', 'status': 'completed')
#   -> Conditions: ["(flow_id = %s)", "(status = %s)"]
#   -> Values: ["HelloFlow", "Completed"]
def resource_conditions(fullpath: str = None) -> (str, MultiDict, List[str], List):
    parsedurl = urlsplit(fullpath)
    query = MultiDict(parse_qsl(parsedurl.query))

    builtin_conditions, builtin_vals = builtin_conditions_query_dict(query)
    custom_conditions, custom_vals = custom_conditions_query_dict(
        query, allowed_keys=None)

    conditions = builtin_conditions + custom_conditions
    values = builtin_vals + custom_vals

    return parsedurl.path, query, conditions, values


async def find_records(request: web.BaseRequest, async_table=None, initial_conditions: List[str] = [], initial_values=[],
                       initial_order: List[str] = [], allowed_order: List[str] = [], allowed_group: List[str] = [],
                       allowed_filters: List[str] = [], postprocess: Callable[[DBResponse], DBResponse] = None,
                       fetch_single=False, enable_joins=False):
    page, limit, offset, order, groups, group_limit = pagination_query(
        request,
        allowed_order=allowed_order,
        allowed_group=allowed_group)

    builtin_conditions, builtin_vals = builtin_conditions_query(request)
    custom_conditions, custom_vals = custom_conditions_query(
        request,
        allowed_keys=allowed_filters)

    conditions = initial_conditions + builtin_conditions + custom_conditions
    values = initial_values + builtin_vals + custom_vals
    ordering = (initial_order or []) + (order or [])

    results, pagination = await async_table.find_records(
        conditions=conditions, values=values, limit=limit, offset=offset,
        order=ordering if len(ordering) > 0 else None, groups=groups, group_limit=group_limit,
        fetch_single=fetch_single, enable_joins=enable_joins, expanded=False
    )

    # Modify the response after the fetch has been executed
    if postprocess is not None:
        results = postprocess(results)

    if fetch_single:
        status, res = format_response(request, results)
        return web_response(status, res)
    else:
        status, res = format_response_list(
            request, results, page, pagination.pages_total)
        return web_response(status, res)