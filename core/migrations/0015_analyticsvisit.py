from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_alter_staffmember_role'),
    ]

    operations = [
        migrations.CreateModel(
            name='AnalyticsVisit',
            fields=[
                ('id', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('visitorId', models.CharField(db_index=True, max_length=120)),
                ('sessionId', models.CharField(db_index=True, max_length=120)),
                ('eventType', models.CharField(db_index=True, default='page_view', max_length=50)),
                ('pagePath', models.CharField(default='/', max_length=255)),
                ('pageTitle', models.CharField(blank=True, default='', max_length=255)),
                ('trafficSource', models.CharField(db_index=True, default='Direct', max_length=80)),
                ('referrerHost', models.CharField(blank=True, default='', max_length=150)),
                ('utmMedium', models.CharField(blank=True, default='', max_length=100)),
                ('utmCampaign', models.CharField(blank=True, default='', max_length=150)),
                ('deviceType', models.CharField(db_index=True, default='Desktop', max_length=30)),
                ('browserName', models.CharField(blank=True, default='Chrome', max_length=50)),
                ('city', models.CharField(blank=True, default='Dhaka', max_length=100)),
                ('country', models.CharField(blank=True, default='Bangladesh', max_length=100)),
                ('isNewVisitor', models.BooleanField(default=True)),
                ('durationSeconds', models.IntegerField(default=0)),
                ('orderId', models.CharField(blank=True, default='', max_length=100)),
                ('productId', models.CharField(blank=True, default='', max_length=100)),
                ('productName', models.CharField(blank=True, default='', max_length=255)),
                ('value', models.FloatField(default=0)),
                ('timestamp', models.CharField(blank=True, default='', max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]

