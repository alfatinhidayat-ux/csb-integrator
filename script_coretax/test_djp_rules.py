def fmt_code(code):
    return str(code).zfill(6)

def fmt_trx(code):
    return str(code).zfill(2)

print(fmt_code(0))
print(fmt_code("0"))
print(fmt_code("A"))
print(fmt_trx(4))
print(fmt_trx("04"))
