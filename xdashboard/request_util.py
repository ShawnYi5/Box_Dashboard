def get_operator(request):
    return request.COOKIES.get("clw_operator", None)
