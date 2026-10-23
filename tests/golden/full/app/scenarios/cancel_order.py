# Желаемое имя/якорь: CancelOrder — не утверждено, можно изменить при написании кода.
# studio: description start
# Отменяет заказ и пишет статус в planned_orders.
# studio: description end
# studio: acceptance start
# - Заказ переходит в статус cancelled
# - Событие отмены сохранено
# studio: acceptance end
# studio: edge start
# from: CancelOrder
# to: PlannedOrder
# kind: uses
# studio: edge end
