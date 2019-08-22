import decimal

from django.core import exceptions
from django.db.models import DecimalField


class MyDecimalField(DecimalField):

    def __init__(self, verbose_name=None, name=None, **kwargs):
        kwargs['max_digits'] = 16
        kwargs['decimal_places'] = 6
        super(MyDecimalField, self).__init__(verbose_name, name, **kwargs)

    def to_python(self, value):
        if value is None:
            return value
        try:
            return float(decimal.Decimal(value))
        except decimal.InvalidOperation:
            raise exceptions.ValidationError(
                self.error_messages['invalid'],
                code='invalid',
                params={'value': value},
            )

    # def get_prep_value(self, value):
    #     value = super(DecimalField, self).get_prep_value(value)
    #     return self.my_to_python(value)

    def my_to_python(self, value):
        v = self.to_python(value)
        if v is None:
            return v
        return decimal.Decimal('{:.6f}'.format(v))

    def get_db_prep_save(self, value, connection):
        return connection.ops.value_to_db_decimal(self.my_to_python(value),
                                                  self.max_digits, self.decimal_places)

    def from_db_value(self, value, expression, connection, context):
        return self.to_python(value)
