var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
  return new bootstrap.Tooltip(tooltipTriggerEl)
})
function relationBtnClick() {
    fetch("/api/relation-type", {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
             "pair": this.dataset.pair ,
             "relation_type": this.dataset.relationtype,
             "operation": this.classList.contains("btn-primary") ? "remove" : "add"
        })
    })
    .then(response => {
        if (response.ok) {
            this.classList.toggle('btn-primary');
            this.classList.toggle('btn-secondary');
        } else {
            alert("Failed to update TEI. Check connection to rdgai server" );
            console.error('Failed to post data:', response.statusText);
        }
    })
    .catch(error => {
        console.error('Error:', error);
    });
}
document.querySelectorAll('.relation-btn').forEach(function(element) {
  element.addEventListener('click', relationBtnClick);
});

document.addEventListener('DOMContentLoaded', (event) => {
    const nextKey = 39; // ArrowRight
    const prevKey = 37; // ArrowLeft
  
    document.addEventListener('keydown', function(e) {
      if (e.keyCode === nextKey) {
        navigateTabs('next');
      } else if (e.keyCode === prevKey) {
        navigateTabs('prev');
      }
    });
  
    function navigateTabs(direction) {
      const activeTab = document.querySelector('.nav-link.active');
      const allTabs = [...document.querySelectorAll('.nav-link')];
      let newIndex = allTabs.indexOf(activeTab);
  
      if (direction === 'next') {
        newIndex = (newIndex + 1) % allTabs.length;
      } else if (direction === 'prev') {
        newIndex = (newIndex - 1 + allTabs.length) % allTabs.length;
      }
  
      allTabs[newIndex].click();
    }
  });
  