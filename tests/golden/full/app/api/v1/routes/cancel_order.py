# Желаемое имя/якорь: cancel_order — не утверждено, можно изменить при написании кода.
# studio: description start
# Принимает запрос на отмену заказа.
# studio: description end
# studio: acceptance start
# - POST /api/v1/users/cancel возвращает 200
# studio: acceptance end
# studio: domain start
# users
# studio: domain end
# studio: edge start
# from: cancel_order
# to: CancelOrder
# kind: dispatch
# studio: edge end
