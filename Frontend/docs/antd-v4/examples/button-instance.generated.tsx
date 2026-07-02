<Button
  type="primary"
  icon={<CheckOutlined />}
  loading={order.submitting}
  disabled={order.items.length === 0}
  onClick={async () => {
    await orderForm.submit();
    message.success('订单提交成功');
  }}
>
  提交订单
</Button>
