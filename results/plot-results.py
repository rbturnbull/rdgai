import plotly.express as px
import pandas as pd
import plotly.io as pio
pio.kaleido.scope.mathjax = None

def format_fig(fig):
    """Formats a plotly figure in a nicer way."""
    fig.update_layout(
        width=1200,
        height=550,
        plot_bgcolor="white",
        title_font_color="black",
        font=dict(
            family="Linux Libertine Display O",
            size=18,
            color="black",
        ),
    )
    gridcolor = "#dddddd"
    fig.update_xaxes(gridcolor=gridcolor)
    fig.update_yaxes(gridcolor=gridcolor)

    fig.update_xaxes(showline=True, linewidth=1, linecolor='black', mirror=True, ticks='outside')
    fig.update_yaxes(showline=True, linewidth=1, linecolor='black', mirror=True, ticks='outside')

    return fig

df = pd.read_csv("arb-results.csv")

df['Accuracy'] = df['Accuracy'].str.rstrip('%').astype('float')/100.0

fig = px.line(df, x="Examples", y="Accuracy", color="LLM", markers=True)

format_fig(fig)

fig.update_yaxes(tickformat=".0%")
fig.update_xaxes(title_text="Number of examples per category")

print("Saving plot to arb-results.pdf")
fig.write_image("arb-results.pdf")