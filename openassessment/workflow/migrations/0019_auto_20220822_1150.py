# Generated by Django 2.2.13 on 2022-08-22 11:50

from django.db import migrations
import model_utils.fields


class Migration(migrations.Migration):

    dependencies = [
        ('workflow', '0018_auto_20220822_0458'),
    ]

    operations = [
        migrations.AlterField(
            model_name='assessmentworkflow',
            name='status',
            field=model_utils.fields.StatusField(choices=[(0, 'dummy')], default='self', max_length=100, no_check_for_status=True, verbose_name='status'),
        ),
    ]