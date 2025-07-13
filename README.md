
# Price Theorem Flask App

## Features
- Receives TradingView alerts
- Connects to Kite API
- Shows if ATM PUT or CALL has highest 5-min volume (mock logic)
- Web dashboard to view results

## Setup

1. Set environment variables:
```
KITE_API_KEY=
KITE_API_SECRET=
FLASK_SECRET_KEY=
```

2. Install packages:
```
pip install -r requirements.txt
```

3. Run the server:
```
python app.py
```

4. Set webhook URL in TradingView:
```
https://yourdomain.com/webhook
```

5. Visit homepage:
```
https://yourdomain.com/
```
